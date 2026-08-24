# D05 子 Agent 与工具系统

> 版本：v0.2（按 review 修正）
> 状态：待 review
> 关联：SPEC v0.3 §4.4、PLAN 会话 F
> 上游：D04（Agent 基础框架）

---

## 1. 目标与边界

### 1.1 目标

定义**子 Agent 体系**：主 Agent 如何通过顶级工具编排子 Agent，执行层工具如何绑定在子 Agent 内部，以及 M0 普通工具调用 → M1+ 分形子图的演进路线。

### 1.2 范围

**包含**：主 Agent 可见工具与执行层工具的边界、四个子 Agent 职责与 M0 实现形态、法律特有痛点的对策、调用协议、人在环中。

**不包含**：具体 skill 的规则库与提示词（D06）、脱敏与文件解析细节（D07）。

---

## 2. 现状

| 资产/问题 | 说明 |
|-----------|------|
| D04 主 Agent | skill 固定步骤 + 步骤内 think-act-observe 循环 + 步骤级工具暴露 |
| D02 检索链路 | knowledge_agent 可复用（query 解析/混合检索/rerank/父子召回/引用校验） |
| 裁判文书检索 MCP | case_agent 的可选联网工具 |
| 无多 Agent 运行数据 | 分形子图设计缺乏实际 trace 支撑 → M0 先普通工具，M1 按 trace 重构 |

---

## 3. 核心架构（M0：普通工具调用）

### 3.1 架构图

```
主 Agent（Supervisor）
├─ 加载 skill（业务动作）
├─ 可见工具 = 4 个顶级工具（子 Agent 粒度）+ skill 步骤声明
└─ 通过普通 tool_call 编排子 Agent
        │
   ┌────┴─────┬──────────┬──────────┐
   ▼          ▼          ▼          ▼
knowledge   case       draft      review
_agent      _agent     _agent     _agent
（普通工具） （普通工具） （普通工具） （普通工具）

每个子 Agent 内部持有自己的执行层工具（如 file_parser、检索函数）
执行层工具不注册给主 Agent
```

### 3.2 设计原则

1. **主 Agent 只绑定分析、编排相关的 skill 和工具**：可见工具 = 4 个顶级工具 + skill 声明的步骤级工具；执行层工具不暴露；
2. **执行层工具绑定在子 Agent 上**：file_parser、底层检索函数、案例库查询等只在子 Agent 内部使用；
3. **证据绑定在工具返回里**：主 Agent 不编造法条/案例，引用溯源由子 Agent 返回结果携带；
4. **State 轻量化**：主 Agent 状态字典只存 `file_id / chunk_ids / summary / last_draft` 等摘要与路径，不存原文；
5. **M0 不做分形子图**：先以普通工具实现，收集实际运行 trace，M1 再汇聚分析、确定哪些子 Agent 值得重构为独立 LangGraph 子图。

### 3.3 路由

- 用户在 UI 显式选择业务动作（skill）——**人在回路路由**；
- 主 Agent 只有 4 个顶级工具，tool_call 候选空间小，意图混淆风险低；
- 执行层工具不注册给主 Agent，进一步消除混淆；
- 未来自由对话入口由主 Agent 基于 skill 注册表与 skill 描述路由，不引入独立意图分类器。

---

## 4. 顶级工具与执行层工具

### 4.1 主 Agent 可见的顶级工具（M0）

| 顶级工具 | 子 Agent | 职责 | 对应业务动作 |
|----------|----------|------|--------------|
| search_law | knowledge_agent | 法规检索、agentic RAG、多子查询 | 法律研究备忘录、案情分析 |
| retrieve_case | case_agent | 案例检索、类案结构化 | 案例检索、法律研究备忘录 |
| draft_doc | draft_agent | 文书模板生成与多轮修改 | 文书生成 |
| review_contract | review_agent | 合同解析、条款审查、报告生成 | 合同审查 |

### 4.2 执行层工具（绑定在子 Agent 内部，不注册给主 Agent）

| 工具 | 所属子 Agent | 说明 |
|------|--------------|------|
| retrieval_service（混合检索+rerank） | knowledge_agent / case_agent | D02 链路 |
| case_store_query | case_agent | 离线案例库查询 |
| wenshu_search（MCP，可选） | case_agent | 裁判文书网检索，实验性 |
| file_parser | review_agent / draft_agent | D01 解析+分块 |
| lexicon_tool | — | 设置页使用，不走主 Agent |

---

## 5. 子 Agent 设计（M0 实现形态：普通工具）

### 5.1 knowledge_agent（法律检索 / agentic RAG）

- **输入**：`{query, corpus_scope, max_results}`；
- **M0 实现**：普通工具函数，内部走 **D02 检索链路**（query 解析 → 混合检索 → rerank → 父子召回）+ 多 query 并行（D02 §8）；
- **返回**：`{summary, chunks:[{chunk_id, source, text_snippet}], citations[]}`；
- **M1 方向**：子查询拆分；根据 trace 决定是否重构为子图。

### 5.2 case_agent（案例检索）

- **输入**：`{query, filters:{案由, 法院, 日期, 案例等级}}`；
- **M0 实现**：普通工具函数，内部走离线案例库检索 + 结构化（案号/案由/法院/裁判日期/争议焦点/裁判要旨）；MCP 可选、限速、降级；
- **返回**：`{cases:[{case_id, case_no, cause, court, date, dispute_focus, ruling_summary, source}], total}`；
- **M1 方向**：类案检索报告结构化深化。

### 5.3 draft_agent（文书生成）

- **输入**：`{doc_type, base_content, modify_instruction?, materials:[file_id...]}`；
- **M0 实现**：普通工具函数，内部：模板选择 → 字段/事实提取 → 生成初稿；**多轮修改**：用户修改指令 → 基于 `last_draft` 重写（最多 3 次）；
- **多轮修改对策**：State 中定义 `last_draft` 与 `draft_history`（Reducer 累积）；修改调用必带 `base_content` + `modify_instruction`；
- **返回**：`{doc_id, doc_type, content_summary, last_draft, draft_history[]}`；
- **M1 方向**：多模板；重写质量评估。

### 5.4 review_agent（合同审查）

- **输入**：`{file_id, review_position: 甲方|乙方|中立}`；
- **M0 实现**：普通工具函数，内部：file_parser → 条款识别（D01 分块结果）→ 简化规则比对（3-5 条示例规则）→ 生成结构化审查报告；
- **返回**：`{report_id, risks:[{clause, risk_level, analysis, suggestion, basis}], summary}`；
- **M1 方向**：深度建工审查规则库（D06）。

---

## 6. 三个法律特有痛点的架构对策

| 痛点 | 对策 | M0 落实 | M1+ 演进 |
|------|------|---------|----------|
| 长文档状态爆炸 | 主 Agent State 只存摘要+路径，不存原文 | 普通工具返回结构化对象 | 子图边界更清晰 |
| 意图混淆 | 双重路由：用户选 skill + 主 Agent 只暴露 4 个顶级工具；执行层工具不注册给主 Agent | 已落实 | 无需 LLM 分类器 |
| 多轮修改状态丢失 | last_draft + draft_history（Reducer）；修改必带 base_content；自循环上限 3 次 | 普通工具内实现 | 重构为子图时保留 |

---

## 7. 调用协议与 Trace

- 主 Agent 通过 `tool_call` 调用顶级工具（`provider=agent`），同步等待返回；
- 子 Agent 执行期间，SSE 推送子 Agent 内部进度摘要（当前子 Agent 名、阶段、进度）；
- 子 Agent 异常：返回结构化错误对象 `{ok:false, error:{code, message, recoverable}}`，不炸主流程；
- **M0 重点**：每次子 Agent 调用完整记录 `tool_calls` 审计（入参/返回摘要/耗时/错误），为 M1 是否重构子图提供数据；
- 子 Agent 返回统一携带 `citations`，最终输出引用由主 Agent 合并去重。

---

## 8. 人在环中（Human Review）

- draft_agent 与 review_agent 产出交付物前，挂起 `human_review` 节点：
  - M0：前端展示预览 + "确认采用 / 发起修改 / 放弃"按钮；用户确认后才进入最终输出；
  - M1：LangGraph Checkpointer 正式挂起（支持暂停、修改参数后继续）。

---

## 9. 版本阶梯

| 版本 | 能力 |
|------|------|
| M0 能跑 | 4 个顶级工具 = 普通工具调用（内部走 D02/简化逻辑）；人在环中为前端确认按钮；工具审计完整 |
| M1 能用 | 基于 M0 trace 分析，将高频/复杂子 Agent 重构为独立 LangGraph 子图；knowledge_agent 子查询拆分；联网搜索 agent（search_web）加入 |
| M2 可控 | 各子 Agent 独立评估集；子图内部 trace 完整；多轮修改行为分析 |
| M3 好用 | 子 Agent 协作优化；修改体验打磨 |
| M4 生产级 | 子 Agent 可插拔、可独立升级；自由对话入口由主 Agent 基于 skill 注册表路由 |

---

## 10. 验收标准

- [ ] 4 个顶级工具注册为 `provider=agent`，主 Agent 可调用；
- [ ] 执行层工具不出现于主 Agent 可见工具列表（注册表隔离验证）；
- [ ] 每个子 Agent M0 普通工具跑通（内部走通各自简化逻辑）；
- [ ] 主 Agent State 不含原文（抽查：合同审查场景 State 只有 file_id/摘要/结构化风险对象）；
- [ ] 多轮修改走通：基于 `last_draft` 修改 ≤ 3 次，`draft_history` 累积；
- [ ] 子 Agent 异常返回结构化错误，主流程不崩；
- [ ] 子 Agent 调用审计完整（入参/返回摘要/耗时/错误），可供 M1 分析；
- [ ] human_review 节点：文书/报告预览 + 用户确认后才输出最终交付物。

---

## 11. 依赖与风险

| 项 | 说明 |
|----|------|
| 普通工具→子图重构 | M1 重构可能改动主 Agent 调用层；通过统一 tool 接口（D04）控制影响面 |
| 同步等待阻塞 | 子 Agent 执行期间主 Agent 等待；SSE 推送进度缓解 |
| State 轻量化边界 | 哪些算摘要哪些算原文需约定；抽查验收防回归 |
| 顶级工具粒度 | 4 个是否够用需实测；M1 按需增删 |
| MCP 合规 | case_agent 接裁判文书网 MCP 需限速+降级+合规提示 |

---

## 12. 开放问题

1. ~~联网搜索 agent 是否 M1 加入~~ **（已决）**：M1 加入 `search_web`；
2. 子 Agent 之间 M0 不允许直接互调，统一由主 Agent 编排（已决）；
3. `draft_history` 保留版本数（M0 全保留，M1 按存储调整）；
4. M1 哪些子 Agent 优先重构为子图：以 M0 trace 的调用频次/失败率/内部复杂度为判据。

---

## 附：决策记录（会话 F，v0.2 修正）

| 项 | 结论 |
|----|------|
| 架构形态 | **M0 普通工具调用**（子 Agent = 普通工具，内部走 D02/简化逻辑）；M1 基于 trace 重构为独立 LangGraph 子图 |
| LLM 意图分类器 | **不做**；路由由用户选 skill（人在回路）+ 主 Agent 顶级工具候选小保证；即使自由对话入口也由主 Agent 基于 skill 注册表路由 |
| 工具暴露 | 主 Agent 可见 = 4 个顶级工具 + skill 步骤声明；执行层工具不注册给主 Agent |
| 路由 | 用户 UI 显式选 skill（人在回路）+ 主 Agent 顶级工具候选小 |
| 状态轻量化 | 主 Agent State 只存摘要+路径，不存原文 |
| 多轮修改 | last_draft + draft_history（Reducer）；修改必带 base_content；自循环上限 3 次 |
| 人在环中 | human_review：M0 前端确认，M1 Checkpointer 挂起 |
| knowledge_agent M0 | D02 检索链路 agentic 封装 + 多 query 并行，不做子查询拆分 |
| 联网搜索 agent | M1 加入 `search_web` |
