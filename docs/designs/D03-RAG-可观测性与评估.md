# D03 RAG 可观测性与评估

> 版本：v0.1（初稿）
> 状态：待 review
> 关联：SPEC v0.2 §4.9、PLAN 会话 D
> 上游：D01（离线索引）、D02（在线检索生成）

---

## 1. 目标与边界

### 1.1 目标

定义**可观测性**（trace 数据模型、存储、前端双视图）与**评估体系**（检索链路评估、报告版本化、防退化风险提示）及 **badcase 收集与归因**的 M0 方案。

### 1.2 范围

**包含**：pipeline trace 全字段、RAG 配置版本号、评估流程、badcase 收集入口与两层归因。

**不包含**：LLM 调用级监控（Langfuse 负责）、生成链路质量评估（M1+ 有黄金评估集后补）、自动化发版阻断（M4 视 CI 情况）。

---

## 2. 现状

| 资产 | 说明 |
|------|------|
| RAG1.0 `PipelineTrace` + `trace_store` | JSONL 持久化 + deque 缓存（最近 200 条） |
| RAG1.0 `interface` trace API | `/api/trace/run` 等，可参考 |
| RAG1.0 `evaluation` 框架 | Click CLI + 指标计算 + 报告导出 |
| `QA_dataset/法律` | 2,254 query / 11,590 qrels（合成，单模型打标偏置） |
| Langfuse 自托管 | M0 记录 LLM 调用级指标 |

---

## 3. Trace 设计

### 3.1 Trace 数据模型

单次问答记录为一条 `pipeline_trace`，按节点时间线组织：

```json
{
  "trace_id": "...",
  "session_id": "...",
  "query": "...",
  "created_at": "...",
  "total_ms": 1234,
  "rag_config_version": "v1",
  "nodes": {
    "prefilter":       {"passed": true, "reason": null, "score": 0.02},
    "query_parser":    {"parsed": {"law_name": "民法典", "article_no": "580", "filter": {}, "exact_match": true, "excluded": []}, "raw_query": "..."},
    "retrieval":       {"filter": {}, "bm25_topk": [{"chunk_id": "", "score": 0}], "dense_topk": [], "rrf_merged": []},
    "difficulty":      {"level": "simple", "rule_hit": "exact_match"},
    "rerank":          {"before_top50_scores": [], "after_topk": [{"chunk_id": "", "score": 0}]},
    "parent_recall":   {"child_hits": [], "parent_ids": [], "truncated": false},
    "context":         {"chunk_count": 3, "total_tokens": 1520, "per_chunk_tokens": [], "truncated": false},
    "generation":      {"model": "deepseek-chat", "latency_ms": 800, "input_tokens": 1800, "output_tokens": 300, "stream": true},
    "citation_check":  {"extracted_refs": [], "verified": [], "expired": [], "unverifiable": [], "rewrites": 0}
  }
}
```

### 3.2 RAG 配置版本号（新增）

- 每次 trace 记录 `rag_config_version` 与 `config_hash`；
- 外部维护 **版本号-配置清单映射表**（SQLite `rag_config_registry`）：

| 字段 | 说明 |
|------|------|
| version | 版本号，格式 `vX.Y`；**配置微调小数点后+1**（v1.1 → v1.2）；不兼容的流程/架构变更才主版本+1（v1 → v2） |
| config_hash | SHA256（规范化 JSON 序列化后的配置关键字段） |
| chunker_config | 分块参数（L_child、overlap、合并阈值等） |
| embed_model | 嵌入模型名/维度 |
| sparse_config | BM25 词典版本 |
| retrieval_config | RRF k、候选数、filter 规则 |
| difficulty_rule_version | 难度规则版本 |
| citation_rule_version | 引用校验规则版本 |
| created_at | 生效时间 |

- 规则：
  1. 任一配置变更 → version 小数点后+1 → 该版本用于后续所有 trace；
  2. 每次评估/实验前先计算 `config_hash`，与 `rag_config_registry` 已有哈希比对：**存在则复用既有版本号与实验结果，避免重复实验**；不存在则新建版本；
  3. 评估报告关联 `version + config_hash`，保证结果可归因、可复现。

### 3.3 Trace 存储

- SQLite 表 `pipeline_traces(trace_id PK, session_id, query, rag_config_version, trace_json, created_at)`；
- **不设内存缓存**：SSE 流式过程中前端实时渲染；历史 trace 从 SQLite 直查（单机 <10ms）；
- Langfuse 自托管只记 **LLM 调用级**（token/成本/延迟/错误），与自研 trace 分工。

### 3.4 前端 Trace 面板（双视图）

| 视图 | 内容 |
|------|------|
| 精简视图（默认） | 步骤卡片时间线（PreFilter→解析→检索→难度→rerank→召回→生成→校验），每步一句话结果；引用卡片可点击定位原文 |
| 技术视图（展开） | **dense/BM25 各自召回 chunk 列表**（chunk_id、得分、文本摘要，可折叠展开全文）、RRF 融合、rerank 分数、filter JSON、难度命中规则、**BM25 对 query 的分词结果（标记命中的用户自定义词典词）**、token 分布、截断标记 |

---

## 4. 评估系统

### 4.1 M0 范围：检索链路评估

- 指标：`Recall@5 / Recall@10 / Recall@20 / MRR / nDCG@10`；
- **M0 评估口径（2026-08-25 定）**：旧 `QA_dataset/法律` qrels 因 chunker 已更换**不再使用**；M0 评估改为**人工抽检**（精确法条号 30 题 + 语义查询 30 题）+ **引用可验证率硬指标**；新 qrels 数据集由用户在后续阶段重建（待办）。
- 引用可验证率仍作为生成侧**硬指标**保留（它是确定性规则校验，不依赖 LLM 裁判，不属于"生成质量评估"）；
- 生成链路质量评估（答案正确性/完整性）M1+ 补，前提是先构建黄金评估集。

### 4.2 评估触发时机

- M0：**仅手动触发** + **索引重建后强制跑一次**；不做自动化/不做发版阻断；
- 理由：当前 qrels 为合成数据且单模型打标有偏置，自动阻断误报率高。

### 4.3 评估报告版本化

- 每次评估输出：
  - `data/eval_reports/{date}_{rag_config_version}.json`（完整指标）
  - 同日期 Markdown 摘要（对比基线、涨跌、风险提示）
- QA_dataset 纳入 git 管理；评估报告不入库（体积小，按日期归档）。

### 4.4 防退化阈值（风险提示，非阻断）

- 基线：重建后首测值；
- 规则：本次 `Recall@10` 低于基线 **2 个百分点（绝对值）** → 报告标红"⚠️ 检索质量显著下降，请人工确认后发版"；
- 当前无自动化发版链路，此阈值只做风险提示；M4 若建立 CI，可升级为阻断条件。

---

## 5. badcase 收集与归因

### 5.1 收集入口（用户层，现场粗分类）

前端两处入口：

| 入口 | 位置 | 收集字段 |
|------|------|----------|
| 标记错误 | 回答下方 | trace_id, query, answer, 错误类型（下拉）, 备注 |
| 引用有误 | 每条引用旁 | trace_id, ref_id, 错误类型（下拉）, 备注 |

错误类型（用户视角）：**检索不对 / 引用错误 / 流程误拦 / 体验问题 / 其他**。

### 5.2 存储

- SQLite `badcases(id, trace_id, session_id, query, answer, error_type_user, ref_id, note, created_at)`；
- 支持导出 JSONL（M0 手动导出）。

### 5.3 归因分类（工程师层，深度根因）

用户在入口处完成**粗分类**；工程师在 badcase 列表中做**根因归因**（M0 手动，M2 自动化）：

| 用户粗分类 | 工程师根因选项 |
|-----------|---------------|
| 检索不对 | BM25 分词问题 / dense 漏召回 / 元数据过滤错误 / 分块问题 / 多 query 合并问题 |
| 引用错误 | 引用校验漏检 / 生成幻觉 / law_meta 数据错误 |
| 流程误拦 | PreFilter 误杀 / 解析器误判 / 难度分档错误 |
| 体验问题 | 格式 / 速度 / 输出太长或太短 |
| 其他 | 待定 |

- 归因结果写入 `badcases.root_cause` 与 `badcases.action`（修复动作）；
- 每周汇总一次根因分布，驱动 M1/M2 迭代优先级。

---

## 6. 版本阶梯

| 版本 | 能力 |
|------|------|
| M0 能跑 | trace 全字段 + 双视图；检索链路手动评估 + 报告版本化；badcase 手动收集 + 工程师手动归因 |
| M1 能用 | 生成链路评估（构建黄金评估集后）；badcase 列表 UI；评估报告自动生成 |
| M2 可控 | badcase 自动化归因；Router/Planner badcase 专项；评估接入发版流程（人工确认） |
| M3 好用 | trace 性能分析面板；评估周报自动推送；badcase 迭代效果量化 |
| M4 生产级 | CI 发版阻断（如需）；评估基准版本化发布；审计级可追溯 |

---

## 7. 验收标准

### 7.1 Trace

- [ ] 每次问答生成完整 pipeline_trace，字段覆盖 §3.1 全部节点；
- [ ] `rag_config_version` 写入，且配置变更后版本号自增；
- [ ] 前端精简视图可读、技术视图完整；引用卡片可定位原文。

### 7.2 评估

- [ ] 索引重建后手动跑通评估，产出 JSON + Markdown 报告；
- [ ] 报告包含基线对比与风险提示（阈值 2pp）。

### 7.3 badcase

- [ ] 前端两个收集入口可用，数据落 `badcases` 表；
- [ ] 至少完成 1 次手动归因（用 demo 期间的律师反馈数据演练）。

---

## 8. 依赖与风险

| 项 | 说明 |
|----|------|
| qrels 合成偏置 | 评估结果可能过于乐观；M1 补真实问题集 |
| chunker 变更与 qrels 错位 | 旧 qrels 标注旧 chunk_id，新 chunker 切分不同无法使用 | M0 跳过 qrels 评估，改人工抽检；新 qrels 由用户后续重建（待办） |
| trace 体积 | 单次 trace JSON 可能数十 KB；SQLite 足够，但需定期清理（M1 加保留策略） |
| 配置版本管理 | 配置分散在多个文件，需在变更入口统一登记，否则版本号失真 |
| 手动评估 | M0 靠人工触发，可能忘记跑；W6 交付文档中写明操作步骤 |

---

## 9. 开放问题

1. trace 保留策略（M1）：保留天数或条数上限待定；
2. 黄金评估集构建方式（律师标注 50-100 条）在 M1 细化；
3. badcase 导出的自动化与对接（M2）。

---

## 附：决策记录（会话 D）

| Q | 结论 |
|---|------|
| Q1 | pipeline_trace 按节点时间线；新增 `rag_config_version`（vX.Y 小数点后+1）+ `config_hash`（SHA256）+ 配置清单映射表，实验前查哈希复用 |
| Q2 | trace 存 SQLite 直查，**不设内存缓存**；Langfuse 只记 LLM 调用级 |
| Q3 | 前端双视图：精简（默认）+ 技术（展开） |
| Q4 | M0 仅手动评估 + 索引重建后强制跑；不做自动 |
| Q5 | M0 只做检索链路评估；引用可验证率为确定性硬指标保留；生成链路评估 M1+ 补 |
| Q6 | 防退化阈值 2pp 仅作风险提示，不阻断；M4 视 CI 升级为阻断 |
| Q7 | QA_dataset 入 git；报告按日期+配置版本归档 |
| Q8 | 收集入口两处 + 用户粗分类下拉 |
| Q9 | 两层归因：用户现场粗分类 + 工程师根因归因（M0 手动，M2 自动化） |
| Q10 | badcases 表 + JSONL 导出 |
