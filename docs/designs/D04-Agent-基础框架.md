# D04 Agent 基础框架

> 版本：v0.1（初稿）
> 状态：待 review
> 关联：SPEC v0.3 §4.4、PLAN 会话 E
> 上游：D02（在线检索生成）、D03（可观测性）

---

## 1. 目标与边界

### 1.1 目标

定义**主 Agent 执行框架**：循环机制（think-act-observe）、工具系统、流式响应、会话管理、上下文压缩、循环防护、日志与 trace。

### 1.2 范围

**包含**：主 Agent 执行模型、skill 步骤编排、工具接口规范与注册表、SSE 事件、会话与上下文管理、防护参数。

**不包含**：子 Agent 拆分与职责（D05）、具体 skill 设计（D06）、脱敏与文件解析（D07）。

---

## 2. 现状

| 资产/问题 | 说明 |
|-----------|------|
| RAG1.0 `online_core.engine.OnlineEngine` | 固定管线，无 Agent 循环；其 trace/生成能力可复用 |
| RAG1.0 `interface.sse_manager` | SSE 流式基础设施，可参考 |
| LangGraph | 未在 RAG1.0 中使用，D04 新引入 |
| 裁判文书检索 MCP | 提供 FastMCP server，作为首个 MCP 工具接入源 |
| 无现成会话管理/上下文压缩 | D04 新写 |

---

## 3. 核心设计决策

### 3.1 执行模型：skill 固定步骤 + 步骤内 think-act-observe 受控循环

```
主 Agent 状态机
├─ 加载 skill（steps 列表）
├─ 按序推进每个 step
│    └─ 步骤内循环（最多 N 轮）：
│         think  → LLM 输出普通文本（自由推理，入 trace）【强制：有 act 必有 think】
│         act    → LLM 输出原生 tool_call（结构化，工具子集由 step 声明）
│         observe→ 工具结果注入消息
│         判定   → 继续循环 / 步骤完成（隐式结束）/ 失败降级
│   【think 规则：步骤首轮强制 think；执行计划形成后的后续工具调用轮允许跳过 think】
└─ 所有 step 完成 → 结果合成
```

**为什么不用传统 ReAct 文本解析**：
- Thought/Action/Observation 文本解析易格式漂移、难结构化、trace 不干净；
- 法律产品要求推理与工具调用都可审计，分开的 think（文本）与 act（结构化 tool_call）更合适。

**为什么不用纯 tool_call（模型只填参数）**：
- 丧失行动前推理能力；
- 但可通过 `think` 阶段补偿——推理以普通 content 输出，不受 JSON 约束。

**分层灵活性原则**：

| 层 | 特性 | 机制 |
|----|------|------|
| 流程层 | 固定 | skill steps 顺序由领域 SOP 决定 |
| 步骤层 | 半固定 | 每步可见工具子集由 skill 声明 |
| 参数层 | 灵活 | 模型决定工具参数、结果取舍、是否重试 |
| 异常层 | 固定 | 框架硬编码：重试→降级→转人工 |

### 3.2 步骤内循环判定（M0：隐式结束为主）

**为什么需要显式判定**：步骤内可多轮调用工具，框架必须知道模型何时认为步骤完成、可以进入下一个 skill step；否则循环无法退出。

**每轮 LLM 返回**：assistant message = `content`（think 文本）+ `tool_calls`（行动）。

**M0 判定规则（隐式结束，主流方案）**：

| 返回情况 | 判定 | 动作 |
|----------|------|------|
| 有 tool_calls | 继续执行 | 执行工具 → observe → 下一轮 |
| 无 tool_calls 且 content 非空 | **步骤结束** | content 即步骤摘要（step_summary），进入下一 skill step |
| 两者都空 | 异常 | 重试 1 次，仍空则步骤失败 |
| 有 tool_calls 但 content 为空 | 见下：think 分级规则 | 步骤首轮 → 不执行工具，发回补 think；执行中轮次 → 允许（执行计划已形成，跳过 think 仅执行） |

**备选方案（M1 视需要）**：`finish_step` 工具——模型主动调用该工具提交结构化步骤摘要。优点结构化、可校验；缺点多占一个工具位。M0 不启用。

**think 分级规则（修正）**：
- **步骤首轮**：强制 think——模型必须输出 content（推理/计划）后才能调用工具；
- **执行中轮次**：允许跳过 think——已形成执行计划后的多轮工具调用中，模型可以只输出 tool_call（content 可为空），框架放行；
- trace 标记每轮 `think_present: true/false`，便于后续分析 think 与执行质量的关系。

**步骤失败**：工具连续失败 2 次 → 框架按 skill 的 `failure_paths` 降级（跳过/重试/转人工）。

### 3.3 工具暴露策略（解决 Schema 污染）

- skill 的每个 step 声明 `visible_tools: [tool_id, ...]`；
- 主 Agent 组装 LLM 请求时，**只注入当前 step 的可见工具 Schema**（2-5 个）；
- 全局工具注册表可以很大（几十个），但单次请求暴露量恒定小。

---

## 4. 工具系统

### 4.1 工具接口规范（Q3）

所有工具统一描述：

```json
{
  "tool_id": "kb_retrieval",
  "name": "知识库检索",
  "description": "在法规库/用户知识库中执行混合检索并返回带引用片段",
  "params_json_schema": { "type": "object", "properties": { "query": {"type": "string"}, "corpus": {"type": "string"} }, "required": ["query"] },
  "result_schema": { "type": "object", "properties": { "chunks": [], "total": {"type": "integer"} } },
  "provider": "local"
}
```

- `provider`：`local`（本地函数）/ `mcp`（外部 MCP server）/ `agent`（子 Agent）；
- 所有工具返回统一包裹：`{"ok": true, "data": ..., "error": null, "latency_ms": 12}`。

### 4.2 工具注册表（Q4）

- 本地 `tool_registry`：注册本地工具 + agent 工具；
- **MCP Adapter**：连接外部 MCP server，动态发现其工具并映射为本地工具（`provider=mcp`）；裁判文书检索 MCP 是首个接入者；
- 工具版本化：`tool_id + version`，变更走注册表。

### 4.3 M0 工具清单（演示用）

| tool_id | 能力 | provider | 来源 |
|---------|------|----------|------|
| kb_retrieval | 知识库混合检索+rerank | local | D02 retrieval_service |
| case_retrieval | 案例检索（离线库） | local | D05 案例 agent（简化版） |
| file_parser | 文件解析为 md + 分块入库 | local | D01 解析管线 |
| doc_generator | 文书/报告模板生成 | local | generation_service |
| lexicon_tool | 查询/维护用户词典 | local | D02 词典服务 |
| wenshu_search | 裁判文书网检索（可选） | mcp | 裁判文书检索 MCP |

---

## 5. 流式 SSE 事件类型（Q5）

| 事件 | 载荷 | 说明 |
|------|------|------|
| session_start | session_id, action, skill_id | 会话/动作开始 |
| step_start | step_id, step_name | 步骤开始 |
| think | text | 模型推理文本（流式） |
| tool_call | tool_id, params | 调用工具 |
| tool_result | tool_id, ok, summary | 工具返回摘要 |
| llm_token | token | 生成 token 流 |
| step_end | step_id, summary | 步骤结束 |
| progress | current, total, percent | 可选，步骤级进度 |
| final | result, citations, disclaimers | 最终结果 |
| error | code, message, recoverable | 错误 |

前端按事件渲染步骤卡片与流式文本。

---

## 6. 会话管理（Q6 修正：用户视图与 Agent 视图分离）

### 6.1 两种视图

| 视图 | 内容 | 用途 |
|------|------|------|
| 用户视图（前端） | 只显示用户消息 + 最终回复；中间过程折叠为步骤卡片（点击展开） | 律师阅读 |
| Agent 视图（发给 LLM） | **全量保留**：user → think → tool_call → tool_result → ... → final，按时间序 | 模型推理需要完整工作记忆 |

### 6.2 消息类型

SQLite `messages(id, session_id, role, msg_kind, content, tool_calls, token_count, created_at)`：

| role | msg_kind | 说明 | 用户视图可见 |
|------|----------|------|:---:|
| user | user | 用户输入 | ✅ |
| assistant | think | 推理文本（强制 think） | 折叠 |
| assistant | tool_call | 工具调用（id+参数） | 折叠 |
| tool | tool_result | 工具返回摘要 | 折叠 |
| assistant | final | 最终回复 | ✅ |

### 6.3 Agent 上下文组装

```
[system prompt + skill 定义 + 当前 step 可见工具 Schema]
[历史摘要（若触发 200k 压缩）]
[会话全量消息（按时间序，直到 200k token 触发压缩）]
```

- 历史窗口**不按条数**，按 token（200k 上限，见 §7）；
- 支持多会话、切换、删除、重命名。

---

## 7. 上下文压缩（Q7）

- 主 Agent 与子 Agent 上下文窗口均按 **200k token** 配置；
- 压缩触发：历史消息总 token 达到 **200k** 时，对**最旧消息做 LLM 摘要**，保留最近 **5 条**原文；
- 摘要以 `role=system` 注入（`历史摘要：...`）；
- 阈值与保留条数**待实测行为特征分布后调整**（D03 trace 已埋点 token 分布）。

---

## 8. 循环防护（Q8）

| 参数 | 值 |
|------|-----|
| 单业务动作最大工具调用轮数 | 50 |
| 单次工具调用超时 | 60 秒 |
| 用户中断 | 前端停止按钮 → 取消流，当前工具调用发取消信号 |
| 工具连续失败降级 | 2 次失败 → 按 skill failure_paths 降级 |
| 总执行时间软上限 | 15 分钟（从用户输入到发起最终回复；超时提示转人工） |

---

## 9. 日志与 Trace

- 主 Agent 全链路 trace 复用 D03 pipeline_trace 结构，新增节点：
  - `skill_load`：skill_id、steps、版本；
  - `step_loop`：每步 think/act/observe 序列、轮数、耗时；
- SSE 事件与 trace 节点一一对应，前端实时渲染后落库可回溯；
- 工具调用入参与返回摘要落 `tool_calls` 审计表（完整返回不落库，超阈值截断）。

---

## 10. 版本阶梯

| 版本 | 能力 |
|------|------|
| M0 能跑 | skill 固定步骤 + think-act-observe 循环；工具注册表 + MCP Adapter；SSE 八类事件；会话管理；200k 压缩；50 轮防护 |
| M1 能用 | 会话历史管理完善；工具失败自动恢复增强；上下文压缩效果评估 |
| M2 可控 | 工具调用全审计；循环行为特征分析（轮数/超时/降级分布）；压缩策略按实测调优 |
| M3 好用 | 错误恢复体验优化；中断/恢复；流式渲染打磨 |
| M4 生产级 | 并发与资源占用优化；崩溃恢复；工具版本管理 |

---

## 11. 验收标准

- [ ] 一个 demo skill（如"法律研究备忘录"桩）走通：加载 skill → 步骤循环 → think → tool_call → observe → 结果合成；
- [ ] SSE 八类事件完整可渲染，前端步骤卡片与流式文本正常；
- [ ] 工具按步骤可见子集注入（验证：某步骤请求 LLM 时 tools 字段只含声明工具）；
- [ ] 工具连续失败 2 次触发降级路径；
- [ ] 会话历史达到 200k token 时触发摘要压缩（单测模拟）；
- [ ] 50 轮上限触发后给出明确终止提示；
- [ ] 用户中断可取消当前执行。

---

## 12. 依赖与风险

| 项 | 说明 |
|----|------|
| LLM function calling 兼容性 | 不同 API 提供商 tool_call 行为有差异；M0 预置 DeepSeek/Qwen 并做适配层 |
| think 规则执行 | 步骤首轮强制 think 增加延迟；执行中轮次放行可能漏掉关键推理；M1 按 trace 分析 think_present 与质量关系调整 |
| 200k 上下文成本 | 窗口大但每次请求 token 成本高；M1 按实测调整压缩阈值与策略 |
| 工具 Schema 版本 | 工具参数变更需同步 skill 声明；走 tool 版本化 |
| 隐式结束误判 | 模型可能想"边说边做"（content + tool_calls 并存）被误判；当前规则按 tool_calls 有无判定，M1 按实测调优 |

---

## 13. 开放问题

1. `finish_step` 工具是否在 M1 启用（当需要结构化步骤摘要时）；
2. 隐式结束对"边说边做"模型的误判率需实测；
3. 全量保留 + 200k 压缩的 token 消耗与延迟需实测调优；
4. MCP Adapter 对 STDIO / SSE 两种传输的支持范围。

---

## 附：决策记录（会话 E）

| Q | 结论 |
|---|------|
| Q1/Q2 | LangGraph + skill 固定步骤 + 步骤内 think-act-observe 受控循环；不做传统 ReAct 文本解析，不做纯 tool_call；步骤级工具暴露防 Schema 污染；异常层硬编码 |
| Q3 | 统一工具接口：tool_id/name/description/params_json_schema/result_schema/provider |
| Q4 | 本地 tool_registry + MCP Adapter（裁判文书 MCP 首个接入） |
| Q5 | SSE 八类事件 + progress 可选 |
| Q6 修正 | 用户视图只显示用户消息+最终回复；Agent 视图全量保留（think/tool_call/tool_result/final）；历史窗口按 token（200k）不按条数 |
| Q1/Q2 补充 | 步骤完成判定 M0 采用隐式结束（无 tool_calls 且 content 非空 = 结束）；think 分级：步骤首轮强制、执行中轮次允许跳过；总执行软上限 15 分钟 |
| Q6 | SQLite messages（role+msg_kind）；多会话；Agent 视图全量保留，用户视图过滤显示 |
| Q7 | 上下文窗口主/子 Agent 均 200k；达 200k 触发摘要，保留最近 5 条；待实测调整 |
| Q8 | 最大工具调用轮数 50；单工具超时 60s；用户可中断；连续失败 2 次降级 |
