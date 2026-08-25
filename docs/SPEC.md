# 法律助手 Demo 技术规格（SPEC）

> 版本：v0.4（草案）
> 状态：待评审
> 适用范围：Demo / MVP 底座阶段（不含深度实务 skill 建设）
> 变更：v0.2 引入资产基线（RAG1.0 + 裁判文书检索 MCP），修正 D2 为 bge-base-zh + BM25，校准评估基线；v0.3 同步 D01-D03 决策；v0.4 同步 D04-D08 决策（think 分级、SKILL.md、M0 普通工具调用、可逆脱敏、docx 首选 python-docx、badcase 闭环），并清理意图分类器表述

---

## 1. 项目概述

### 1.1 项目定位

面向**执业律师 / 企业法务**的本地化法律 AI 助手桌面应用。Demo 阶段目标是搭建一个**可视、可感、可体验的技术底座**，验证四件事：

1. **法律 RAG 质量**：混合检索 + rerank + 父子召回 + 引用可验证；
2. **多模式显式路由**：用户在界面显式选择工作模式，不做意图识别；
3. **主从式多 Agent 框架**：应用层（skill）与工具层（tool agent）分层清晰，业务动作可插拔；
4. **全程可观测**：检索过程、工具调用、生成依据逐项可见、可回溯。

### 1.2 资产基线

Demo **不重复造轮子**，以下列已有资产为基线整合改造：

| 基线 | 来源 | 复用内容 |
|------|------|----------|
| RAG1.0 检索/引擎 | `D:\个人\Research\RAG1.0` | `offline_core`（分块/索引/增量）、`online_core`（引擎/PreFilter/Reranker）、`evaluation`（评估框架）、`data/indices/法律/qdrant`（现成 hybrid 索引）、`QA_dataset/法律`（2,254 q / 11,590 qrels）、`local_model/`（本地模型） |
| 裁判文书检索 MCP | `D:\个人开发\裁判文书检索MCP` | MCP 工具定义与场景化检索逻辑、登录/加密/令牌组件（作为可选联网工具，非主数据源） |

### 1.3 Demo 范围

**包含：**

- Tab1 知识库问答：法规库问答，引用溯源，trace 可见；
- Tab2 实务助手：业务动作入口（案例检索 / 案情分析 / 合同审查 / 法律研究备忘录），框架真实、skill 为桩实现；
- 用户知识库管理：上传、解析、分块、入库、元数据过滤检索；
- 用户自定义关键词（检索期分词增强）；
- 规则版脱敏 + 免责声明 + 审计日志；
- Windows 开箱即用交付形态。

**明确不做（Demo 阶段）：**

- 不实现深度实务 skill（合同审查等只做流程桩）；
- 不做意图识别（用户显式选择模式）；
- 不做知识图谱；
- 不做 OCR / 扫描件解析；
- 不做模型微调；
- 不做移动端；
- 不做 Router 全量激活（按 RAG1.0 实验结论默认关闭，PlannerEstimator 保留接口不启用）。

### 1.4 术语

| 术语 | 含义 |
|------|------|
| 主 Agent | 面向用户的业务编排 agent，负责解析需求、加载 skill、编排工具、合成结果 |
| Tool Agent | 面向主 Agent 的能力提供者，封装重型工具（知识库检索、案例检索、文件解析等） |
| Skill | 应用层业务动作的声明式定义：流程 + 决策 + 分析 + 工具调度 |
| 业务动作 | 实务助手 tab 中对律师暴露的功能入口，如案例检索、案情分析、合同审查 |
| 父子召回 | 检索命中子块后返回父文档（完整节/条组），子块提供精确锚点 |
| 混合检索 | BM25 稀疏向量 + dense 向量（bge-base-zh）的 RRF 融合召回 |
| qrels | 评估用查询-相关文档标准集 |
| 资产基线 | 上表所列两个已有项目的代码/索引/模型/数据，作为 demo 起点 |

---

## 2. 用户与场景

### 2.1 目标用户

- **主用户**：执业律师（建工领域优先）、企业法务；
- **使用方式**：本机安装运行，个人/单用户使用；
- **技术能力假设**：会安装 Windows 软件、会填 API Key，不懂 Docker、不碰命令行。

### 2.2 核心用户故事

| 编号 | 故事 | 验收要点 |
|------|------|----------|
| U1 | 律师打开应用，在知识库问答 tab 输入法律问题，得到带法条引用的回答 | 引用可点击定位原文，无编造条文 |
| U2 | 律师在问答页查看检索过程 | 能看到 query 解析、检索 chunk、BM25/dense 得分、rerank 前后变化 |
| U3 | 律师进入实务助手 tab，选择"合同审查"业务动作，上传一份合同 | 主 Agent 按 skill 流程调度工具，流程与工具调用在 trace 中可见 |
| U4 | 律师上传自有文档建立个人知识库 | 支持 md/docx/pdf/txt，解析后仅本人可检索，元数据可控范围 |
| U5 | 律师在设置中添加自定义关键词（如"实际施工人"） | 保存后下次检索立即生效，BM25 不再切碎该词 |
| U6 | 律师在设置中更新 LLM API Key | 热更新，无需重启应用 |
| U7 | 律师启动应用，系统自动检查法律库更新 | 有更新则增量爬取、切块、重建索引，前台可见进度 |
| U8 | 律师输入的文本含姓名/身份证/手机号 | 入库与送 LLM 前自动脱敏 |

---

## 3. 系统架构

### 3.1 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Web UI（React，桌面浏览器）                  │
│  Tab1 知识库问答 │ Tab2 实务助手 │ 设置 │ 知识库管理 │ Trace 面板 │
└──────────────────────────────┬───────────────────────────────┘
                               │ REST / SSE
┌──────────────────────────────▼───────────────────────────────┐
│                    FastAPI 应用层                              │
│  /chat  /assistant  /kb  /case  /lexicon  /config  /trace     │
│  统一鉴权（本机单用户 token）│ 审计日志 │ 配置热更新             │
└────────────┬─────────────────────────────┬────────────────────┘
             │                             │
   ┌─────────▼─────────┐          ┌────────▼─────────┐
   │  知识库问答线      │          │   实务助手线       │
   │  (基于 OnlineEngine)│         │   (LangGraph)     │
   │  query解析        │          │  主 Agent         │
   │  PreFilter       │          │  ├─ skill 注册表   │
   │  混合检索         │          │  └─ 任务编排       │
   │  rerank          │          │  工具调用          │
   │  父子召回         │          │  结果合成          │
   │  引用校验         │          │        │          │
   │  生成             │          │   Tool Agent(s)   │
   └────────┬─────────┘          │  ├─ 知识库检索工具  │
            │                    │  ├─ 案例检索工具    │
            │                    │  ├─ 文件解析工具    │
            │                    │  └─ 文书生成工具    │
            └──────────┬─────────┴────────┬───────────┘
                       │                  │
              ┌────────▼──────────────────▼─────────┐
              │           共享服务层                  │
              │ retrieval_service | kb_service       │
              │ case_service | lexicon_service       │
              │ generation_service | file_parser     │
              │ update_service（法规库增量更新）       │
              └────────┬──────────────────┬─────────┘
                       │                  │
              ┌────────▼────────┐  ┌──────▼────────┐
              │ Qdrant（本机）   │  │ SQLite（本机） │
              │ 向量 + 稀疏      │  │ 配置/知识库/审计│
              └─────────────────┘  └───────────────┘
               文件存储：data/（用户上传、模型、日志）
```

### 3.2 两条主链路

**链路 A：知识库问答（确定性管线，基于 OnlineEngine 适配）**

```
用户问题
  → Query 解析器（法规名/条文号/效力级别 → 元数据过滤 + 精确匹配）
  → LegalPreFilter（拦截闲聊/非法律；kb_vocab 路径修复后开启）
  → 混合检索（dense=bge-base-zh + BM25 sparse，RRF，带 filter）
  → Reranker 精排（bge-reranker-v2-m3）
  → 父子召回（返回完整父文档）
  → LLM 生成（仅基于召回文本；Router 默认关闭）
  → 引用校验器（逐条验证法条引用存在且现行有效）
  → 输出（回答 + 引用列表 + trace）
```

**链路 B：实务助手（Agent 编排）**

```
用户选择业务动作 + 输入材料
  → 主 Agent 加载对应 skill
  → skill 定义流程/决策/工具调度
  → 主 Agent 调用 Tool Agent 暴露的工具
  → 工具返回结构化结果
  → 主 Agent 合成交付物
  → 输出（结果 + 过程 trace）
```

### 3.3 分层铁律

1. **主 Agent 不直接访问数据层**（不直接查 Qdrant / SQLite / 文件）。所有知识访问必须通过 Tool Agent 暴露的工具接口。
2. **Skill 是可插拔声明**：新增业务动作 = 新增/修改 skill 定义，不改主 Agent 框架代码。
3. **两条线共享同一 retrieval_service**：知识库问答的检索能力和 Tool Agent 暴露的检索工具是同一个服务，不重复实现。
4. **所有生成必须基于召回证据**：无召回到证据的条文引用，输出前被引用校验器拦截。

### 3.4 关键设计决策

| 编号 | 决策 | 内容 |
|------|------|------|
| D1 | LLM 调用 | 仅 API 调用，OpenAI 兼容接口；预置 Qwen / DeepSeek 等国内服务商；API Key 前端设置热更新，本地保存 |
| D2 | Embedding | **bge-base-zh（768d，dense）+ BM25 sparse**，沿用 RAG1.0 现有 hybrid 索引与评估最优配置；BGE-M3 升级列入 Backlog |
| D3 | Reranker | bge-reranker-v2-m3 本地部署 |
| D4 | BM25 分词 | jieba + 内置法律词典（索引期）+ 用户自定义词典（查询期） |
| D5 | 向量库 | Qdrant，单 collection 多语料，metadata 隔离（开发连已有索引，交付内置子进程） |
| D6 | 业务数据库 | SQLite（SQLAlchemy 抽象，预留 PG 迁移） |
| D7 | 案例数据 | 离线结构化案例库为主，裁判文书网 MCP 为可选联网工具（实验性） |
| D8 | 文件解析 | docx 首选 python-docx（结构保真），Markitdown 保底；pdf Markitdown 首选，PyMuPDF 保底；扫描件 M0 拒绝；分块复用 RAG1.0 `StructureAwareChunker` + `ParentChildChunker`；不做 OCR |
| D9 | 前端 | React + Vite + Ant Design，桌面端 only；trace 展示参考 RAG1.0 React SPA |
| D10 | 可观测性 | Langfuse 自托管 + 前端自研 Trace 面板（复用 RAG1.0 trace API 设计） |
| D11 | 交付形态 | Windows 安装包（Inno Setup），启动器拉起 Qdrant + FastAPI，浏览器访问 127.0.0.1 |
| D12 | 脱敏 | 规则版先上（姓名/手机号/身份证号/公司名/地址/银行卡号），本地小模型 NER 纳入排期 |
| D13 | 代码基线 | 以 RAG1.0 `offline_core` / `online_core` / `evaluation` / `interface` 为基线整合，不另起炉灶 |
| D14 | Router | 知识库问答线默认关闭 Router 全量激活；PlannerEstimator 保留接口不启用；badcase 分析列入 Backlog |

### 3.5 资产复用与适配关系

| 资产 | 复用方式 | 适配改造 |
|------|----------|----------|
| `data/indices/法律/qdrant` | 直接作为公共法规库索引 | 核对 collection schema；如需重建，按目标分块策略重跑 pipeline |
| `offline_core.store.QdrantStore` | 检索底层 | 统一配置路径；暴露为 `retrieval_service` |
| `offline_core.retriever.HybridMethod` | 混合检索 | 包装统一接口；支持 metadata filter |
| `online_core.reranker.CrossEncoderReranker` | 精排 | 无重大改造 |
| `online_core.legal_pre_filter.LegalPreFilter` | 前置过滤 | 修复 `kb_vocab_path` 指向实际词表 |
| `online_core.engine.OnlineEngine` | 问答引擎 | Router 默认关闭；替换 system prompt；接引用校验器 |
| `evaluation.*` | 评估回归 | 修复 `SearchQuery(query_id=...)` 兼容 bug |
| `QA_dataset/法律` | 评估集 | 直接使用；后续补真实问题 |
| `offline_core.incremental_indexer` | 法规库增量更新 | 先 `rebuild_full()` 重建 manifest，使增量可用 |
| 裁判文书检索 MCP | 案例可选联网工具 | 抽离检索逻辑为 tool agent 工具；限速、降级、合规提示；凭据不迁入 demo 仓库 |

---

## 4. 模块设计

### 4.1 前端（Web UI）

**页面结构：**

| 路由 | 页面 | 内容 |
|------|------|------|
| `/` | 知识库问答 | 对话流 + 引用卡片 + 右侧 Trace 抽屉 |
| `/assistant` | 实务助手 | 业务动作卡片（案例检索/案情分析/合同审查/法律研究备忘录）；点击进入动作页：输入区 + 流程步骤可视化 + 结果区 |
| `/kb` | 知识库管理 | 上传文件、知识库列表、文档状态、检索范围设置 |
| `/settings` | 设置 | LLM 配置（Base URL / API Key / Model，热更新）、自定义关键词管理、脱敏开关、法律库更新状态与手动触发 |
| `/trace` | Trace 面板 | 按会话查看完整链路：query 解析 → 检索结果（chunk/score）→ rerank 前后 → 工具调用 → 生成与引用校验 |

**交互要求：**

- 引用卡片：回答中每条法条引用可点击，弹窗显示法规全文片段（定位到条/款）；
- Trace 抽屉：问答页右侧可展开，不遮挡主对话区；
- 设置热更新：API Key 等配置保存即生效，无需重启；
- 自定义关键词交互：参考"弹幕过滤"体验——输入框 + 回车添加 + 标签列表，支持删除、批量导入、启用/停用；
- 前端技术栈与 trace 展示可参考 RAG1.0 `interface/frontend`（React 19 + Vite + TS），但 UI 按本 demo 两个 tab 重做。

### 4.2 后端应用层（FastAPI）

- 单机单用户，启动时生成本机 token，前端持有；
- 统一审计：所有 /chat /assistant 请求记录 `user_id, session_id, mode, input(脱敏后), output 摘要, trace_id, 时间, 模型`；
- 配置服务：读写本地配置（LLM 配置、脱敏规则、词典），支持热更新通知；
- SSE 流式输出：问答与实务助手均支持流式返回生成过程。

### 4.3 知识库问答线

#### 4.3.1 Query 解析器（新写）

输入用户问题，输出结构化查询对象：

```json
{
  "original_query": "民法典第580条说了什么",
  "law_name": "民法典",
  "article_no": "580",
  "effect_level": null,
  "publish_dept": null,
  "filter": {"law_name": "民法典", "article_no": "580"},
  "exact_match": true
}
```

规则：
- 识别 `法规名 + 第N条` 模式 → 构造精确 filter；
- 识别效力级别（法律/行政法规/司法解释/部门规章）→ 转元数据 filter；
- 无精确匹配信息 → 走语义检索，filter 仅保留 corpus 范围；
- 解析结果展示在 trace 中；
- **否定/排除与多候选**：候选前紧邻否定/排除词（不是/并非/除了/排除/而非/不包括等）→ 标记 `excluded` 不作 exact_match；多候选同法规用 OR 过滤，跨法规退化语义检索（详见 D02 §3.3）。

#### 4.3.2 混合检索（复用 HybridMethod）

- **BM25**：jieba 对 query 分词（内置法律词典 + 用户自定义词典），生成稀疏向量，Qdrant sparse index 检索；
- **Dense**：query 经 bge-base-zh 编码，dense 向量检索；
- **融合**：RRF（k=60），复用 `offline_core.retriever.HybridMethod` 并封装为统一服务；
- **过滤**：所有检索带 metadata filter（`corpus` / `user_id` / `kb_id` / `law_name` / `article_no` / `effect_level` / `status` / `doc_type` 等）。

#### 4.3.3 Rerank（复用 CrossEncoderReranker）

- 对融合后候选（top-50 左右）用 bge-reranker-v2-m3 精排；
- trace 记录 rerank 前后顺序与分数变化；
- 精排后按**难度分档**保留 top-k：simple 5 / medium 8 / hard 10，难度由规则判别器 `DifficultyEstimator` 输出（详见 D02 §4.2）。

#### 4.3.4 父子召回

- 命中子块后，返回其父文档（完整节/条组）；
- 父文档作为生成上下文，子块 id 作为引用定位锚点；
- **超长 parent 处理**：parent ≤ 1,500 token 完整返回；超长则返回"命中 child + prev/next 邻接 child"拼成局部上下文（≤ 1,500 token）；
- 前端引用定位：先到父文档，再高亮命中的子块片段；
- 同 parent 多 child 命中合并去重（详见 D02 §4.4）。

#### 4.3.5 引用校验器（新写，生成后置闸门）

- 从 LLM 输出中抽取所有条文引用（正则 + 结构化解析：`法规名 + 第X条 + 款/项`）；
- 逐条在法规库中精确匹配：
  - 存在且 `status=现行有效` → 通过，附文档 id；
  - 存在但已失效 → 标记"失效"并提示；
  - 不存在 → 打回重写 1 次（发回上一轮完整输出 + 未验证清单）；重写后仍失败 → 不打回不删除，在原输出后追加"⚠️ 未能验证的引用"风险提示；
- **验收硬指标：最终输出引用可验证率 100%**（通过 / 已标注失效 / 已追加未验证提示，不允许静默编造）。

#### 4.3.6 生成

- Prompt 约束：只依据召回文本作答；禁止使用未提供的条文；不确定时明说；
- Router 默认关闭，不经过 PlannerLLM；
- 输出格式：正文 + 引用列表（`法规名 第X条` + 原文链接）+ 固定免责声明；
- 流式输出。

#### 4.3.7 LegalPreFilter（复用）

- 拦截闲聊/无意义/非法律 query，零 RAG 开销直接回复；
- 必须修复 `kb_vocab_path` 指向 `data/indices/法律/kb_vocab.json`（RAG1.0 已知坑）。

### 4.4 实务助手线

#### 4.4.1 主 Agent 与 Skill 注册表

- 主 Agent 职责：接收业务动作名 + 用户输入 → 从 skill 注册表加载 skill → 按 skill 流程节点推进 → 调用工具 → 合成结果；
- **Skill 注册表**：`skills/*/SKILL.md`（YAML frontmatter + Markdown 正文），启动时加载；frontmatter 存机器字段，正文为自然语言流程/决策/失败处理，由 LLM 作为 skill 指令读取；前端业务动作列表由注册表动态生成；
- 主 Agent 状态机字段：`{action, skill, current_step, step_results, tool_calls, trace}`。

#### 4.4.2 Skill 定义结构

```markdown
---
skill_id: contract_review
name: 合同审查
input_schema: {...}
visible_tools: [review_contract]
steps: [...]
---

# 合同审查

## 流程
...（律师/法务自然语言编写）
## 决策思路
...
## 失败处理
...
```

Demo 阶段：`steps` 允许为**桩实现**——工具真实存在并返回真实结果，但 `decision/analyze` 用简化 Prompt 占位，不承诺业务深度。

#### 4.4.3 Tool Agent 与工具注册表

- **主 Agent 可见工具 = 4 个顶级工具（子 Agent 粒度）+ skill 步骤声明**；执行层工具绑定在子 Agent 内部，不注册给主 Agent；
- **M0 子 Agent = 普通工具调用**（内部走 D02 检索链路或简化逻辑）；M1 基于 M0 运行 trace 分析，将高频/复杂子 Agent 重构为独立 LangGraph 子图（分形架构，详见 D05）；
- **路由**：用户 UI 显式选 skill（人在回路）+ 主 Agent 顶级工具候选小；未来自由对话入口由主 Agent 基于 skill 注册表路由，不引入独立意图分类器。

| 顶级工具 | 子 Agent | 能力 | 底层 |
|----------|----------|------|------|
| search_law | knowledge_agent | 法规检索、agentic RAG、多子查询 | D02 retrieval_service |
| retrieve_case | case_agent | 案例检索、类案结构化 | 离线案例库 / 裁判文书网 MCP 适配器 |
| draft_doc | draft_agent | 文书模板生成与多轮修改 | generation_service |
| review_contract | review_agent | 合同解析、条款审查、报告生成 | file_parser + 规则比对 + doc_generator |

执行层工具（不注册给主 Agent）：`retrieval_service`、`case_store_query`、`wenshu_search`（MCP 可选）、`file_parser`、`lexicon_tool`（设置页使用）。

#### 4.4.4 Demo 阶段业务动作

| 动作 | Demo 实现程度 |
|------|--------------|
| 案例检索 | 真实调用 case_retrieval 工具（离线库为主；MCP 可选） |
| 案情分析 | 桩：主 Agent 按 skill 流程调用 kb_retrieval + 简化分析 Prompt |
| 合同审查 | 桩：主 Agent 按 skill 流程调用 file_parser + kb_retrieval + doc_generator 的简化版 |
| 法律研究备忘录 | 桩：流程同上（列入，便于律师朋友评估价值） |

### 4.5 数据管道（法规库）

- **现状**：RAG1.0 `data/indices/法律/qdrant` 已就绪（471 份法规 docx，7,339 chunks，bge-base-zh dense + BM25 sparse，Recall@10=0.823）；
- **目标分块策略**（SPEC 定义）：
  - 以**节**为最小结构单位；小块合并；超长分点条款保留首部语义字段（类表格首行）；
  - 非章-节格式退化为自然段落 + 标点切分；
  - 遵循均分原则（块大小均匀优先于恰好不超长）；
- **Gap 分析**：W1 对照上述目标检查 `StructureAwareChunker` 实现与现有索引实际切分，输出 Gap 清单；已决策 M0 重建（同 collection 全量替换，旧索引备份 `qdrant_v1_backup` 作评估对照，详见 D01 §2.2）；
- **父子文档**：父 = 完整节/条组，子 = 均匀分块；若现有索引不满足则重建；
- **增量更新**：应用启动时后台执行 `update_service`：
  1. 拉取国家法律库最新文件元数据；
  2. 与本地元数据比对，识别新增/修订/废止；
  3. 对变化文件增量爬取 → 切分 → 生成向量 → 更新索引；
  4. 更新本地元数据与 `status` 标记（现行有效/已修订/已废止）；
  5. 前台展示进度，失败不影响主功能（下次启动重试）；
  - 前置条件：先通过 `IncrementalIndexer.rebuild_full()` 重建 manifest（现有 manifest `files` 为空，无法增量）。
- **元数据**：`law_name, article_no, effect_level, publish_dept, status, version, chapter, section, parent_id, doc_type, department, valid_status` 等，建 payload index。

### 4.6 用户知识库

- **上传**：md / docx / pdf / txt，单文件大小上限（默认 20MB）；
- **解析**：docx 首选 python-docx（结构保真），失败降级 Markitdown；pdf Markitdown 首选，PyMuPDF 保底；扫描件 M0 拒绝并提示；
- **分块**：复用 `StructureAwareChunker`（结构优先，第X条原子单元）+ `ParentChildChunker`（生成父子对）；
- **入库**：写入同一 Qdrant collection，`corpus=user` + `user_id` + `kb_id`；
- **隔离**：所有检索强制过滤 `(corpus=public) OR (corpus=user AND user_id=当前用户)`；用户选择检索范围时进一步限定 `kb_id`；
- **管理**：文档列表（解析状态/块数/时间）、删除（同步删向量与文件）、重解析。

### 4.7 案例服务

- **离线案例库（主）**：
  - Demo 数据：最高法指导性案例 + 公报案例 + 朋友提供的脱敏建工判决书；
  - 结构化字段：案号、案由、法院、裁判日期、当事人（脱敏）、争议焦点、裁判要旨、原文；
  - 使用同一 collection（`corpus=case`）与检索管线，元数据支持按案由/法院/日期过滤。
- **裁判文书网 MCP（辅）**：
  - 作为 tool agent 可选工具，标注"实验性/合规自评"；
  - 复用 MCP 项目的场景化检索逻辑（案号/当事人/法条/说理/指导案例）与加密令牌组件；
  - 限速（默认每分钟 1 次）、超时降级、失败提示转离线库；
  - 不纳入默认检索范围；凭据（账号/密码/Cookie）不迁入 demo 仓库，由用户自行配置。

### 4.8 词典服务

- **内置法律词典**：随应用发布（`experiments/data/legal_dict.txt`），用于索引期和查询期分词；
- **用户自定义关键词**：仅作用于**查询期分词**，不改索引、不重算文档向量；
- 实现：每次检索前 jieba 加载 `内置词典 + 用户启用词典`；词典变更即时生效；
- 前端交互：弹幕过滤式——输入词 → 回车 → 标签展示 → 可删/停用/批量导入；
- 限制：单用户词典上限（默认 500 词），单词长度 ≤ 50 字符。

### 4.9 可观测性

- **Langfuse 自托管**：记录 LLM 调用、token、延迟、成本、错误；
- **前端 Trace 面板**（自研，面向律师可读，双视图）：
  - 精简视图（默认）：步骤卡片时间线 + 每步一句话结果 + 引用卡片可点击定位原文；
  - 技术视图（展开）：chunk_id、BM25/dense/RRF/rerank 分数、filter JSON、难度命中规则、token 分布、截断标记；
  - 问答线节点：query 解析 → 检索（BM25/dense 各自 top-k 与得分）→ RRF 融合 → 难度分档 → rerank 前后 → 父子召回 → 生成上下文 → 引用校验；
  - 实务线节点：skill 步骤 → 工具调用参数/返回摘要 → 每步耗时 → 最终结果；
- **RAG 配置版本号**：每次 trace 记录 `rag_config_version`（格式 vX.Y：配置微调小数点后+1，不兼容变更主版本+1）与 `config_hash`（SHA256）；外部维护 `rag_config_registry` 版本-配置-哈希映射，实验前查哈希复用，避免重复实验（详见 D03 §3.2）；
- **Trace 存储**：SQLite `pipeline_traces` 直查，不设内存缓存；
- **日志**：本地 `data/logs/`，滚动保留 30 天。

### 4.10 安全与脱敏

- **规则版脱敏（Demo 上线，可逆假名化）**：正则识别并替换——姓名、手机号、身份证号、公司名称、地址、银行卡号；替换为 `[姓名1]`、`[手机号1]` 等带类型序号占位；映射表 Fernet 加密存本机，密钥分离；仅最终交付物输出前还原，trace/日志不还原；
- **脱敏位置**：用户输入送 LLM 前、用户文档解析入库前、审计日志写入前；
- **本地小模型 NER 脱敏**：纳入排期（Backlog），用于识别规则难覆盖的机构名、项目名；
- **免责声明**：所有输出固定尾部声明"本结果由 AI 生成，不构成正式法律意见，使用前须经执业律师核阅"；
- **越界拦截**：Prompt 层 + 输出层拦截"规避法律"等请求，转人工建议；
- **配置安全**：API Key 仅存本地 `data/config.json`，不出本机；MCP 凭据由用户自行配置并加密存储。

---

## 5. 数据模型

### 5.1 Qdrant Collection

**collection 名**：沿用 RAG1.0 现有 `chunks`（公共库 + 用户库 + 案例库共用）。

**向量**：
- `dense`：bge-base-zh，维度 768（现有索引）；
- `sparse`：BM25 稀疏向量（jieba 分词 + IDF）。

**payload 关键字段**：

| 字段 | 说明 | 索引 |
|------|------|------|
| chunk_id / doc_id | 块与文档标识（确定性 id） | 是 |
| corpus | public / user / case | 是 |
| user_id | 用户库归属 | 是 |
| kb_id | 知识库 id | 是 |
| law_name | 法规名 | 是 |
| article_no | 条文号 | 是 |
| effect_level | 效力级别 | 是 |
| publish_dept | 发布部门 | 是 |
| status / valid_status | 现行有效/已修订/已废止 | 是 |
| version | 法规版本 | 是 |
| chapter / section | 章/节 | 否 |
| heading_path | 结构路径 | 否 |
| parent_chunk_id | 父文档 id | 是 |
| chunk_level | parent / child | 是 |
| prev_chunk_id / next_chunk_id | 前后邻接 | 否 |
| doc_type | 文档类型 | 是 |
| content_type | text/table/code/image_placeholder | 否 |
| department | 发布部门 | 是 |
| case_no / case_type / court / case_date / dispute_focus | 案例专用 | 按需 |

### 5.2 SQLite 表

| 表 | 用途 |
|----|------|
| sessions | 会话记录（session_id, mode, action, created_at） |
| audit_logs | 审计日志 |
| user_kb | 用户知识库（kb_id, name, created_at） |
| user_docs | 文档（doc_id, kb_id, file_path, parse_status, chunk_count, created_at） |
| user_lexicon | 自定义关键词（id, term, enabled, created_at） |
| config | 应用配置（LLM base_url, api_key, model, 脱敏开关等） |
| law_meta | 法律库元数据（law_id, law_name, version, status, last_updated, source_url） |
| update_jobs | 增量更新任务记录 |

### 5.3 文件存储

```
data/
├─ config.json            # 配置（含 API Key）
├─ uploads/               # 用户上传原文件
├─ parsed/                # 解析后 md
├─ indices/               # Qdrant 索引（公共库 + 用户库）
├─ models/                # bge-base-zh / bge-reranker-v2-m3 / PreFilter 模型
├─ logs/                  # 应用日志
└─ sqlite.db              # 业务库
```

---

## 6. API 设计（关键端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/chat | 知识库问答（SSE 流式），入参：query, session_id, kb_scope |
| POST | /api/assistant | 实务助手（SSE 流式），入参：action, files, params, session_id |
| GET | /api/actions | 业务动作列表（来自 skill 注册表） |
| GET | /api/trace/{session_id} | 会话完整 trace |
| POST | /api/kb/upload | 上传文档 |
| GET | /api/kb/docs | 文档列表 |
| DELETE | /api/kb/docs/{id} | 删除文档 |
| POST | /api/kb/reparse/{id} | 重新解析 |
| GET | /api/case/search | 案例检索 |
| GET/POST | /api/lexicon | 查询/添加/删除自定义关键词 |
| GET/PUT | /api/config | 读取/更新配置（热更新） |
| GET | /api/update/status | 法律库更新状态 |
| POST | /api/update/run | 手动触发法律库更新 |

---

## 7. 部署方案

### 7.1 交付形态：Windows 安装包

- **打包**：Inno Setup 制作安装包；
- **安装后目录**：

```
LegalAssistant/
├─ LegalAssistant.exe      # 启动器/托盘
├─ server/                 # FastAPI 应用 + 前端静态文件
├─ qdrant/                 # Qdrant server 二进制（随包）
├─ models/                 # 首次启动下载 bge-base-zh / bge-reranker-v2-m3
└─ data/                   # 用户数据目录（索引、SQLite、上传、日志）
```

### 7.2 启动流程

1. 用户双击 `LegalAssistant.exe`；
2. 启动器检查端口、拉起 `qdrant.exe` 子进程；
3. 启动 uvicorn（FastAPI + 前端静态文件）；
4. 自动打开浏览器 `http://127.0.0.1:{port}`；
5. 后台启动法律库增量更新检查（`update_service`），托盘显示进度；
6. 托盘常驻，可退出/打开/查看更新。

### 7.3 模型分发

- 安装包不含模型（bge-base-zh 约 391M + reranker 约 2.2G，共约 2.6G）；
- 首次启动从 ModelScope 下载到 `data/models/`；
- 下载失败时给出明确提示与重试，不影响已索引数据（仅检索功能暂不可用）；
- 开发阶段直接复用 RAG1.0 `local_model/`。

### 7.4 开发模式

- 开发者本地：FastAPI 连现有 Qdrant 索引（RAG1.0 `data/indices/法律/qdrant` 或 demo 数据目录），前端 Vite dev server；
- 与交付模式共用同一 collection schema 与代码。

### 7.5 硬件基线

- Windows 10/11 x64，内存 ≥ 16GB，磁盘空闲 ≥ 15GB（含模型下载与索引）。

---

## 8. 测试与验收

### 8.1 检索质量

- **评估集**：RAG1.0 `QA_dataset/法律`（2,254 query / 11,590 qrels）；
- 指标：Recall@5、Recall@10、Recall@20、MRR、nDCG@10；
- **M0 评估口径**：旧 qrels 因 chunker 变更不再使用；M0 改人工抽检（精确法条号/语义查询各 30 题）+ 引用可验证率硬指标；新 qrels 由用户后续重建（详见 D03 §4.1）；
- **防退化风险提示**：后续评估若 Recall@10 低于基线 2 个百分点（绝对值）→ 报告标红，人工确认后发版（M4 视 CI 可升级为阻断）；
- 已知局限：qrels 为合成数据且单模型打标存在偏置，需以律师真实问题补充（Backlog P0）。

### 8.2 引用可验证性

- 测试集：200 条知识库问答回答；
- **硬指标：回答中法条引用可验证率 = 100%**（可验证或已标注无法验证，不允许静默编造）。

### 8.3 实务助手框架

- 每个业务动作桩：能走通"加载 skill → 调度工具 → 返回结果 → trace 完整"；
- 工具调用成功率 ≥ 95%（测试环境）。

### 8.4 用户知识库

- 上传 md/docx/pdf/txt 各 ≥ 1 份，解析入库成功；
- 不同 kb_id 隔离有效（A 库文档在限定 B 库检索时不出现）；
- 自定义关键词添加后立即影响 BM25 分词（以分词结果接口验证）。

### 8.5 交付验收

- 全新 Windows 机器（或虚拟机）安装包安装后，双击即可用；
- 断网时：应用可启动，法律库更新检查失败不影响已有知识库问答；
- 联网时：法律库更新检查正常执行。

---

## 9. 风险与合规

| 风险 | 说明 | 应对 |
|------|------|------|
| 裁判文书网 MCP 合规/稳定 | 自动化访问可能违反网站条款，且反爬会导致失效 | 离线案例库为主；MCP 标记实验性、限速、降级；凭据不随包分发，用户自配并自评合规 |
| 法条引用幻觉 | LLM 编造条文 | 引用校验器 + 无证据不引用 + 可验证率 100% 验收 |
| 法规时效 | 引用已废止法规 | 元数据 status 过滤 + 增量更新维护 status |
| 客户数据保密 | 律师上传真实案件材料 | 本机处理；规则脱敏；数据不出本机（除调用所选 LLM API 的部分） |
| 本机性能 | 模型 + Qdrant 占用内存 | 16GB 基线；reranker FP16；后续模型量化/降配 |
| 模型下载失败 | 首次启动依赖外部源 | ModelScope 国内源 + 重试 + 断网降级提示 |
| 基线路径/配置坑 | RAG1.0 存在路径不一致、manifest 为空等已知问题 | W1 显式配置修复；增量更新前 rebuild_full |
| 凭据泄露（MCP 项目） | `.env`/cookies 含明文凭据 | 不迁入 demo 仓库；demo 加密存储；加 .gitignore |

---

## 10. 开放问题（待后续迭代确认）

1. 多用户支持：Demo 为单用户，数据模型已预留 `user_id`，API 暂不做多用户；
2. 离线案例库的公开数据源最终选择与授权确认；
3. 合同审查等深度 skill 的建设排期（Demo 后根据律师反馈启动）；
4. 本地小模型 NER 脱敏的具体模型选型；
5. 法规库增量更新的爬取频率与定时策略（当前为启动时检查一次）；
6. Router badcase 分析：用户认为 RAG1.0 的 Router 结论可能因缺少 trace 分析而失真，需对 `hybrid_vs_router.csv` 与 traces 做逐条归因后重新评估（Backlog P0）。
