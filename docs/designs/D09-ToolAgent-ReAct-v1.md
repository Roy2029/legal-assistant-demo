# D09 Tool Agent（ReAct v1）开发文档

> 版本：v0.1（细化开发稿）
> 状态：待 review
> 关联：D04（Agent 基础框架）、D05（子 Agent 与工具系统）、D02（检索链路）
> 上游输入：`tool agent设计.md`（用户 2026-08-28 提供）
> 实现策略：**v1 先以原生 ReAct 循环实现 RAG agent（knowledge_agent）；类案检索 agent 先落接口与渐进式读取骨架，MCP 后接**

---

## 1. 目标与边界

### 1.1 目标

在 `legal-assistant-demo` 内实现一个 **tool agent 运行时**，让知识库检索从“单次 query → 检索 → 回答”升级为：

```
案情要点/法律问题
  → RAG agent（ReAct 循环：规划 → 检索 → 评估 → 优化重试）
       → 检索编排层（并行多查询 + 融合/分组汇聚）
       → 知识库（公共库 + 用户文件夹，元数据过滤）
  → 资料检索报告（查询思路 + 过程 + 回答 + 引用）
```

v1 的 RAG agent 是 **knowledge_agent 的内部实现升级**，对外仍是主 Agent 的一个顶级工具（D05 §4.1 的 `search_law`）。

### 1.2 范围

**v1 包含**：
- ReAct 运行时：原生 `tool_calls` 循环、停止条件、超时/重试/上限
- RAG 工具：`kb_search`（检索编排）、`kb_index`（知识库概览）、`read_file` / `write_file`（结果持久化）
- 检索编排层：并行子查询、按组融合（fuse）或分组返回（separate）
- 知识库选择：全部 / 仅公共 / 指定用户文件夹 / 公共+指定用户文件夹（OR 过滤）
- 自动上下文压缩
- 资料检索报告生成
- Trace 事件（SSE）与落库

**v1 不包含**：
- 类案检索 agent 的完整 MCP 爬虫接入（先定义接口与元数据渐进式读取骨架）
- 主 Agent 的自由 ReAct（主 Agent 仍按 D04：skill 固定步骤 + 步骤内 think-act）
- LLM 意图分类器

### 1.3 与现有设计的关系

| 层 | 现有设计 | 本文档作用 |
|---|---|---|
| 主 Agent | D04：skill 固定步骤 + 步骤内 think-act | 不变；`search_law` 顶级工具的内部实现升级 |
| 子 Agent | D05：knowledge_agent 是普通工具 | v1 把 knowledge_agent 内部改成 ReAct agent |
| 检索链路 | D02：RetrievalService.search/search_multi | 作为执行层引擎，被编排层调用 |
| 上下文 | server/context_compressor.py | 复用并增强为 agent 循环内的压缩器 |

---

## 2. 总体架构

```
主 Agent（Supervisor）
  └─ skill step: retrieve_law
       └─ tool_call: search_law(query, task_type, folders)
            └─ RAG agent（ReAct loop）            ← 本文档实现
                 ├─ think（规划/评估）
                 ├─ act（调用工具）
                 │    ├─ kb_index()               → 知识库概览
                 │    ├─ kb_search(plan)          → 检索编排层
                 │    ├─ read_file(path)          → 读中间结果
                 │    ├─ write_file(path, content)→ 持久化中间结果
                 │    └─ finish(report, answer, citations, needs_human)
                 └─ observe（工具结果注入）
                 
RAG agent 内部工具不注册给主 Agent（D05 §3.2）。
```

### 2.1 检索编排层

```
kb_search(plan)   // RAG agent 调用的工具
  ├─ 解析 plan：groups = [{group_id, merge_mode, queries:[...], folders, top_k}]
  ├─ 对每个 group：
  │    ├─ merge_mode="fuse"：同 query 多角度分解 → 并行检索 → RRF 融合去重
  │    └─ merge_mode="separate"：对比/总结类 → 并行检索 → 各自保留，不打散
  ├─ 所有 group 并行执行
  └─ 返回 {group_results:[{group_id, merge_mode, results:[...]}], stats}
```

`kb_search` 内部最终调用 `RetrievalService.search()`（D02 链路），不是另起炉灶。

---

## 3. ReAct 循环设计（v1）

### 3.1 循环模型

采用 **OpenAI 原生 function calling**，不采用文本解析 Thought/Action/Observation。

原因：
- 我们的 LLM 是 DeepSeek（OpenAI 兼容），支持 `tools` / `tool_calls`；
- 文本解析易格式漂移、trace 不干净；
- D04 已定调：`think` 用普通 content 文本，`act` 用结构化 tool_call。

### 3.2 消息结构（每一轮）

```
messages = [system, *history(压缩后)]
loop:
  response = llm.chat_with_tools(messages, tools, tool_choice="auto")
  if response.tool_calls:
      # 可选 think 文本
      content = response.content  # 模型可输出简短推理，也可为空
      messages.append(assistant(content, tool_calls))
      for tc in tool_calls:
          result = execute(tc)          # 带超时/重试
          messages.append(tool_result(tc.id, result))
      continue
  else:
      # 无 tool_calls：必须已有 finish 或达到兜底
      break
```

### 3.3 停止条件（按优先级）

| 条件 | 动作 |
|---|---|
| `finish` 工具被调用 | 正常结束，取结构化报告 |
| 连续 2 轮无 tool_calls 且 content 非空 | 视为结束，content 作为回答草稿 |
| 连续 3 次工具失败 | 注入 system-reminder，强制重新规划 |
| `MAX_ITERATIONS = 10` | 强制结束，输出已完成部分 + needs_human=true |
| 单轮循环总时长 > `LOOP_TIMEOUT=180s` | 强制结束，输出已完成部分 + needs_human=true |
| 用户取消（SSE 断开） | 停止循环，写 trace |

### 3.4 think 规则

- 首轮**强制 think**：模型必须先输出规划文本再调用工具；框架检测首轮只有 tool_calls 无 content 时，发回 system-reminder 要求补规划；
- 执行中轮次允许跳过 think；
- 每轮记录 `think_present`，写入 trace。

---

## 4. 工具定义（RAG agent 可见）

### 4.1 kb_index

```json
{
  "name": "kb_index",
  "description": "返回当前知识库的文件夹列表、文档数、chunk 数、示例文档名。用于决定该去哪个知识库查。",
  "parameters": {"type":"object","properties":{},"required":[]}
}
```

返回：
```json
{
  "public": {"name":"公共法律库","docs":471,"chunks":17598,"doc_types":["law"]},
  "user_folders": [{"folder":"default","docs":7,"chunks":120}, {"folder":"施工合同","docs":3,"chunks":45}],
  "note": "用户文件夹按 metadata.folder 过滤；公共库按 corpus=public 过滤"
}
```

### 4.2 kb_search（检索编排层工具）

```json
{
  "name": "kb_search",
  "description": "在知识库中执行一组检索计划。每组可包含多个子查询；同一问题多角度分解的子查询用 merge_mode=fuse 融合，对比/总结类子查询用 merge_mode=separate 分别保留。",
  "parameters": {
    "type": "object",
    "properties": {
      "groups": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "group_id": {"type":"string"},
            "merge_mode": {"type":"string","enum":["fuse","separate"]},
            "queries": {"type":"array","items":{"type":"string"}},
            "folders": {"type":"array","items":{"type":"string"},"description":"空=全部；__public__=公共库；其他=用户文件夹名"},
            "top_k": {"type":"integer","default":8}
          },
          "required": ["group_id","merge_mode","queries"]
        }
      }
    },
    "required": ["groups"]
  }
}
```

返回：
```json
{
  "groups": [
    {
      "group_id": "g1",
      "merge_mode": "fuse",
      "results": [
        {"chunk_id":"...","law_name":"...","article_no":"...","text":"...","score":0.32,"source":"public|user"}
      ]
    }
  ],
  "stats": {"elapsed_ms":1230,"groups":2,"queries":4}
}
```

### 4.3 read_file / write_file

```json
{"name":"read_file","parameters":{"path":{"type":"string"}}}
{"name":"write_file","parameters":{"path":{"type":"string"},"content":{"type":"string"}}}
```

- 根目录固定为 `data/agent_workspace/{session_id}/`，工具层负责拼绝对路径并**禁止越权**（拒绝 `..`）；
- v1 只允许读写 `.md` / `.json` 文件，单文件写入上限 200KB。

### 4.4 finish

```json
{
  "name": "finish",
  "description": "提交资料检索报告并结束。",
  "parameters": {
    "type":"object",
    "properties":{
      "report":{"type":"string"},
      "answer":{"type":"string"},
      "citations":{"type":"array","items":{"type":"object","properties":{"law_name":{"type":"string"},"article_no":{"type":"string"},"chunk_id":{"type":"string"}}}},
      "needs_human":{"type":"boolean","default":false}
    },
    "required":["report","answer"]
  }
}
```

---

## 5. 检索编排层（重点）

### 5.1 过滤器构造

知识库选择逻辑：

| 用户选择 | Qdrant Filter |
|---|---|
| 空（全部） | 无过滤 |
| `["__public__"]` | `metadata.corpus = public` |
| `["folderA"]` | `metadata.corpus = user AND metadata.folder = folderA` |
| `["__public__","folderA"]` | **OR**：`should = [corpus=public, (corpus=user AND folder=folderA)]` |

> 当前 `RetrievalService.search()` 只支持 public / user 单选，需要在 v1 增加 `scope` 或 `folders` 的 OR 过滤。实现位置：`online_core/retrieval_service.py` 的 filter 构造处。

### 5.2 fuse 融合

- 同组子查询并行调用 `RetrievalService.search()`；
- 子查询结果用 RRF 融合（与 D02 一致，`k=60`），去重后取 `top_k`；
- 返回结果保留每条的来源 query（`from_query`），trace 中可回溯。

### 5.3 separate 分组

- 对比/总结类子查询（如“违约金上限” vs “定金规则”）不能融合；
- 每个子查询各自返回 `top_k`，结果按 group 原样保留，由 RAG agent 阅读后归纳。

### 5.4 并行与超时

- 子查询并行：`ThreadPoolExecutor(max_workers=min(len(queries), 6))`；
- 单个检索超时 20s；超时返回空结果 + error 标记，不中断整组；
- 每轮 `kb_search` 总超时 90s。

---

## 6. 知识库概览注入

- **静态注入**：RAG agent 启动时，`kb_index` 结果写入 system prompt 末尾；
- **动态刷新**：循环中允许调用 `kb_index` 工具（知识库可能刚上传文档）；
- system prompt 模板：

```
你是法律资料检索 agent。当前知识库概览：
- 公共法律库：471 份法规，17598 chunks。适合查法条、法理、生效信息。
- 用户文件夹：
  - default：7 份文档，120 chunks。
  - 施工合同：3 份合同，45 chunks。适合查合同条款。
检索原则：
1) 先 kb_index 或根据概览确定查哪个库；
2) 复杂问题拆成多组子查询，用 kb_search 并行查；
3) 同问题多角度用 fuse，对比/总结用 separate；
4) 检索不足时：查询回退（去修饰词）、查询具体化（补法条号/案由）、多角度分解；
5) 重要中间结果用 write_file 落盘；
6) 最后用 finish 交报告，引用必须来自检索结果。
```

---

## 7. 上下文管理

### 7.1 工具结果截断

- `kb_search` 返回的每个 chunk 文本最多 400 字，总返回不超过 12,000 字；超出在 stats 中标注截断；
- `read_file` 单次最多读 8,000 字，超出部分通过 offset/limit 读取（v1 先读前 8,000 + 提示）。

### 7.2 循环内压缩

- 复用 `server/context_compressor.py` 的估算逻辑，阈值改为 **60,000 tokens**（DeepSeek 保守值，后续按模型上下文调整）；
- 压缩策略：保留 system + 最近 4 条消息；更早的 tool_result 用结构化摘要替换（`[已压缩] tool=kb_search groups=2 results=12 ...`）；
- v1 无 LLM 摘要时，用规则生成摘要占位。

---

## 8. Trace 与可观测性

### 8.1 SSE 事件（在现有 `/api/assistant` 事件上扩展）

| 事件 | 载荷 | 说明 |
|---|---|---|
| agent_start | agent=knowledge, task | RAG agent 启动 |
| agent_think | text | ReAct 推理文本（流式） |
| agent_plan | groups | 规划摘要（从 kb_search 入参提取） |
| agent_tool_call | tool, params | 工具调用 |
| agent_tool_result | tool, ok, summary | 工具结果摘要 |
| agent_retry | reason, attempt | 重试提示 |
| agent_report | report, answer, citations | 最终报告 |
| agent_error | code, message, needs_human | 错误 |

### 8.2 落库

- 复用 `pipeline_traces` 表：`trace_id, session_id, query, rag_config_version, trace_json`；
- `trace_json` 保存完整 ReAct 轨迹（每轮 think/tool_call/tool_result/耗时）。

---

## 9. 类案检索 agent（v1 接口与骨架）

### 9.1 定位

类案检索 agent 也是主 Agent 的顶级工具 `retrieve_case`。v1 只做离线骨架，MCP 爬虫就绪后替换数据源。

### 9.2 渐进式读取工具

| 工具 | 说明 |
|---|---|
| `case_search` | 按案由/关键词/法院/日期检索，返回**元数据页**（总数、案号、标题、案由、法院、日期，不返回全文） |
| `case_read` | 按 case_id 读取文书指定段落（默认先返回“本院认为”前 800 字） |
| `case_summary` | 对单篇文书生成结构化摘要（焦点/说理/裁判结果） |

### 9.3 结果过多策略

- `case_search` 返回 `total` 和 `cursor`；
- 总数 > 200 时，system prompt 强制要求 agent 优化关键词（缩小案由/法院/日期范围）后再查；
- 每轮 `case_read` 最多读 5 篇，避免上下文爆炸。

---

## 10. 可靠性设计

| 机制 | v1 参数 |
|---|---|
| 单工具超时 | kb_search 90s；kb_index 10s；read/write 15s |
| 单工具重试 | 最多 2 次，指数退避 1s/2s |
| 连续失败熔断 | 3 次 → 注入 system-reminder，要求重新规划 |
| 循环上限 | 10 轮 |
| 循环总超时 | 180s |
| 结果不足判定 | 所有 group 返回 0 条或 top1 score < 阈值 → agent 应改写查询重试 |
| 人工介入 | `finish(needs_human=true)` 或循环超时 → 返回主 Agent 时带 `needs_human`，前端提示“检索不充分，是否继续/转人工” |
| 费用保护 | v1 只允许 `kb_search` 每轮最多 3 组、每组最多 4 个 query |

---

## 11. 实现阶段与任务拆解

### 阶段 1：LLM 工具调用能力
- [ ] `server/llm.py` 增加 `chat_with_tools(messages, tools, stream=False)`：OpenAI 兼容 `/chat/completions` + `tools`，返回 `{content, tool_calls:[{id,name,arguments_json}]}`
- [ ] 单元测试：mock OpenAI 响应解析

### 阶段 2：检索层 OR 过滤
- [ ] `RetrievalService.search()` 增加 `folders: list[str]` 参数，支持 `["__public__"]`、用户文件夹、公共+用户文件夹的 OR 过滤
- [ ] 单元测试：三种过滤组合

### 阶段 3：检索编排层
- [ ] `online_core/search_orchestrator.py`：`orchestrate(groups) -> group_results`
- [ ] fuse（RRF）与 separate 两种模式；并行执行；超时隔离
- [ ] 单元测试：fuse 去重、separate 保留分组

### 阶段 4：RAG agent ReAct 运行时
- [ ] `online_core/agents/rag_agent.py`：循环、停止条件、熔断、上下文压缩
- [ ] 工具注册：kb_index / kb_search / read_file / write_file / finish
- [ ] 工作目录：`data/agent_workspace/{session_id}/`
- [ ] 单元测试：mock LLM 场景（正常结束/连续失败/超上限）

### 阶段 5：知识库概览注入
- [ ] `kb_index` 实现：读 Qdrant + SQLite 统计公共库与用户文件夹
- [ ] system prompt 动态生成

### 阶段 6：报告与 Trace
- [ ] `finish` 报告模板：查询思路 / 查询过程 / 回答 / 引用
- [ ] SSE 事件扩展；`pipeline_traces` 落库

### 阶段 7：类案检索 agent 骨架
- [ ] `case_search/case_read/case_summary` 接口 + 离线空实现 + 渐进式读取测试

### 阶段 8：集成与验收
- [ ] 主 Agent `search_law` 顶级工具接 RAG agent
- [ ] `/api/assistant` 事件前端展示
- [ ] 全量测试 + 5 个真实法律问题冒烟

---

## 12. 测试与验收

| 验收项 | 标准 |
|---|---|
| 工具调用解析 | 10/10 mock 用例通过 |
| 检索过滤 | 全部/公共/用户文件夹/混合 4 种组合正确 |
| 编排层 | fuse 去重正确；separate 分组正确；单查询超时不拖垮整组 |
| ReAct 循环 | 正常 finish、3 次失败熔断、10 轮强制结束、总超时 4 条路径可复现 |
| 上下文压缩 | 60k 阈值触发，压缩后循环不丢 system 和最近 4 条 |
| 报告 | 报告含查询思路/过程/回答/引用；引用 chunk_id 可定位 |
| 真实问题冒烟 | 5 个法律问题，检索报告可用，引用可验证率 100% |

---

## 13. 开放问题 / 待确认

1. **DeepSeek function calling 的并发 tool_calls 数量**：v1 先按串行执行 tool_calls，避免并发副作用；是否允许并行后续看实测。
2. **模型上下文**：deepseek-chat 上下文窗口以官方最新为准，60k 阈值是保守值，上线前需确认。
3. **类案 MCP 合规与稳定性**：裁判文书网爬虫可能不稳，v1 类案数据源是否先用手工导入的离线案例 JSONL？
4. **主 Agent 与 RAG agent 的 think 重叠**：D04 主 Agent 也有 think，RAG agent 内部 think 是否要在前端都展示，还是只展示 RAG agent 的规划与报告？建议：主 Agent think 展示，RAG agent 只展示 plan/tool/report，避免刷屏。
5. **workspace 文件清理**：`data/agent_workspace/` 是否需要按会话结束自动清理，还是保留 7 天？建议保留 7 天，M2 再做看板。
