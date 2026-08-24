# RAG1.0 项目索引

> 勘察路径：`D:\个人\Research\RAG1.0`（bash: `/d/个人/Research/RAG1.0`）
> 勘察时间：2026-08-19
> 用途：为 legal-assistant-demo 技术规格（SPEC.md）提供可复用资产盘点

---

## 1. 项目概览

RAG1.0 是一个**法律领域检索增强生成（RAG）研究/实验项目**，核心是“三层闸门”在线管线（LegalPreFilter 规则层 → PlannerEstimator ML 层 → PlannerLLM 路由层）+ Qdrant 混合检索（dense + BM25 sparse）+ Cross-Encoder 精排，并配套离线索引构建、QA 数据集合成、检索评估框架与可观测前端。

- **技术栈**：Python 3.10+，FastAPI + React 19/Vite/TypeScript（前端），Qdrant（嵌入式模式，统一存 dense+sparse），sentence-transformers（bge-base-zh / bge-m3 / Qwen3-Embedding-0.6B / bge-reranker-v2-m3），jieba BM25 稀疏编码，scikit-learn Logistic 回归，SQLite + Click 评估 CLI。
- **数据规模**：法律知识库 471 份 .docx 法规文件（外部目录 `D:\github-repo\法律数据库爬虫\laws_files\法律`，不在本仓库），生成 7,339 个 chunk；QA 数据集 2,254 条 query / 11,590 条 qrels。
- **研究阶段结论**：
  1. **Hybrid(bge-base+BM25) 是当前最佳检索方案**：Recall@10=0.823、Recall@20=0.944（Oracle K=20=100%），显著优于纯 dense 和纯 BM25。
  2. **Router/Planner 全量激活曾是负优化**（平均仅返回 8.9 条，Recall@20 停滞在 0.762）；后引入 PlannerEstimator 选择性激活（A∪B 策略，激活率 ~47% 捕获 ~95.8% 收益）。
  3. 法律 PreFilter（V2+）在 530 条测试上 F1=0.922（P=0.881, R=0.967），可零 LLM 成本拦截非法律 query。
  4. 项目仍是研究型代码，部分脚本未跟上 Qdrant 迁移，存在路径/参数不一致。

---

## 2. 目录结构（2-3 层，忽略 .venv/node_modules/__pycache__/回收箱/_archived）

```
RAG1.0/
├── README.md                    # 项目主文档（架构/组件/演进/实验结论）
├── LawRefBook_snapshot.md       # LawRefBook GitHub 页的可访问性树快照（Playwright 产物）
├── run_experiment.py            # 离线索引 + 50 文档 QA 合成实验（旧 FAISS 路径）
├── run_qa_law.py                # 法律库 QA 数据集生成（MultiModelRetriever，当前 QA_dataset 来源）
├── run_recall_eval.py           # BM25/召回批量评估脚本（注意：存在 query_id 参数 bug）
├── verify_hybrid_improvement.py # hybrid vs sparse 单点验证（注意：存在 query_id 参数 bug）
├── requirements*.txt / .env / .gitignore / pytest.ini
├── offline_core/                # 离线：解析→切块→嵌入→索引
│   ├── pipeline.py              # 离线流程编排（Qdrant 写入、摘要点、chunks.html 导出）
│   ├── parser.py / pdf_parser.py / docx_parser.py
│   ├── chunker.py               # StructureAwareChunker + ParentChildChunker
│   ├── embedder.py              # HuggingFace Embedding + 内容哈希缓存
│   ├── store.py                 # QdrantStore、BM25Encoder（jieba）、旧 FAISS/BM25 兼容
│   ├── retriever.py             # HybridMethod + Simple/Filter/Hierarchical/ParentChild 策略
│   ├── enricher.py / manifest.py / chunk_export.py / incremental_indexer.py / multi_model_retriever.py
├── online_core/                 # 在线：三层闸门→检索→重排→生成
│   ├── engine.py                # OnlineEngine 全流程编排（含 trace() 可观测入口）
│   ├── legal_pre_filter.py      # 法律领域规则层（关键词+正则+非法律语境排除）
│   ├── planner_estimator.py     # ML 激活判定器（21 维特征 + Logistic A∪B）
│   ├── query_router.py          # V1 路由（保留兼容）
│   ├── query_router_v2.py       # PlannerLLM（V2 Prompt，预算意识）
│   ├── strategy_dispatcher.py   # 策略编排 + Collector 去重
│   ├── reranker.py / context_manager.py / session_manager.py / trace_store.py / llm.py
├── evaluation/                  # rag-eval 检索评估框架
│   ├── cli.py / config.py / pipeline.py / runner.py / metrics.py
│   ├── registry.py / cache.py / reporter.py / analyze.py / analyze_topk.py
├── QA_synthesis/                # QA 数据集合成（生成→去重→打标→校验）
│   ├── pipeline.py / generator.py / deduplicator.py / labeler.py / validator.py
│   ├── batch_client.py / checkpoint.py / monitor.py / retry.py / models.py / config.py
├── interface/                   # Web 界面
│   ├── fastapi_app.py / api_routes.py / cli.py
│   ├── frontend/                # React SPA（TraceWorkbench / QAAnalyzer / History / Settings）
│   ├── templates/ / static/     # 旧版 Jinja2/HTMX UI（挂 /legacy）
│   └── log_capture.py / sse_manager.py / task_manager.py / trace_store.py
├── experiments/                 # 实验代码与结果
│   ├── analysis/analysis_report.md   # 关键：实验数据分析报告
│   ├── filter_optimization/          # PreFilter 优化（含 REPORT.md、eval_result.txt）
│   ├── planner-utility-estimator/    # Planner 激活器实验（模型、报告、特征）
│   ├── topk-*/ hybrid-*/ online-pipeline/  # 各检索实验（experiment.yaml + metadata.json + results/）
│   ├── hybrid_vs_router.csv          # 2253 query × 70 字段对比数据（Planner 实验来源）
│   └── scripts/ / data/ / experiments.db
├── QA_dataset/法律/             # 交付数据集：queries.json + qrels.json + quality_report
├── data/
│   ├── indices/法律/            # 多模型 Qdrant 索引 + chunks.html + kb_vocab.json + manifest.json
│   ├── datasets/                # QA 合成中间产物（generated/deduped/labeled）
│   ├── qa-reports/ / traces/ / logs/ / sessions/ / test_10docs_output/
├── config/                      # default.yaml + user.yaml + runtime.json + ConfigManager
├── scripts/                     # 操作脚本（build_index / build_multi_index / query / rag-eval / start_web ...）
├── utils/                       # cost_tracker / llm_monitor / token_estimator
├── tests/                       # 473 个 test_*（offline/online/online_core/qa_synthesis/utils）
├── docs/                        # 设计文档、使用手册、参考文章
├── openspec/                    # 变更提案/规格（spec-driven，含 archive 与 2 个 active change）
├── local_model/                 # 本地模型（9.1G，见数据资产）
└── logs/                        # 运行日志（QA 合成日志 94M）
```

---

## 3. 关键文件与文档

| 路径 | 说明 |
|---|---|
| `README.md` | 主文档：三层闸门架构图、组件清单、检索策略、Phase 1-3 演进、关键实验发现、快速开始 |
| `docs/usage/检索评估链路使用手册.md` | 评估框架用法、数据格式（queries/qrels/labeled_records）、CLI 参数、输出结构 |
| `docs/usage/使用手册.md` | QA 合成流水线用法、环境变量、断点重续 |
| `docs/usage/OFFLINE_USAGE.md` | 离线处理指南（部分内容为旧 FAISS/Chroma 时期，需甄别） |
| `docs/designs/Planner Utility Estimator需求与实验设计.md` | ML 激活器需求、标签设计、阈值策略 |
| `docs/designs/RAG可观测前端重构设计.md` | React 前端 + JSON API + trace 设计 |
| `docs/designs/路由模块实现方案.md` | 路由-检索策略规划、Router 输出约定 |
| `docs/designs/合成QA数据集.md` | QA 合成目标、query 类型定义、四阶段流程 |
| `docs/designs/qdrant升级.md` / `解析-切块升级.md` / `FastAPI前后端.md` | 架构演进需求 |
| `experiments/analysis/analysis_report.md` | **最重要的实验数据报告**：数据集画像、TopK/模型/Router 消融结论 |
| `experiments/filter_optimization/REPORT.md` | LegalPreFilter 优化过程与最终指标 |
| `experiments/planner-utility-estimator/reports/classifier/comprehensive_experiment_report.md` | Planner 预过滤分类实验完整报告 |
| `experiments/hybrid_vs_router.csv` | 2253 query 的 hybrid vs router 逐条对比（70 字段），Planner 实验数据源 |
| `openspec/readme.txt` | openspec 工作流入口 |
| `openspec/changes/multi-model-ensemble-qa/proposal.md` | 多模型 ensemble 召回打标（active change） |
| `openspec/changes/project-github-ready-cleanup/proposal.md` | GitHub 发布前清理（active change） |
| `openspec/changes/archive/*` | 20+ 历史变更（增量索引、rerank、评估框架、三层闸门等） |
| `LawRefBook_snapshot.md` | GitHub LawRefBook 仓库页面可访问性树快照（614 行，非法律正文） |
| `.claude/` / `.agents/` | 仅含 openspec 工作流命令/技能，无项目专属 agent 配置 |
| `.env` | API 密钥（DeepSeek、阿里百炼、OpenAI、Gemini 等），已被 .gitignore 排除，不可入库 |

---

## 4. 核心代码模块

| 模块 | 职责与关键实现点 |
|---|---|
| `offline_core.chunker` | `StructureAwareChunker`：heading stack 维护结构路径；法律“第X条”作为原子单元不可切分；max_chars=1000；code/table/image 独立成 chunk。`ParentChildChunker`：parent≈1000 字符/overlap 200，child≈250/overlap 50，建立 parent↔child 与前后邻接关系 |
| `offline_core.manifest` | 确定性 `doc_id = sha256:<hash>`、`chunk_id = chunk:<16hex>`；Manifest 记录多模型索引元数据；`scan()` 检测新增/修改/删除；`bm25.dirty` 标记 |
| `offline_core.store` | `QdrantStore`：嵌入式/服务式、单 collection 同时存 dense + sparse；`BM25Encoder` 用 jieba 分词 + IDF 生成 Qdrant SparseVector；支持 payload index、scroll、按 doc_id 删除、按 chunk_id 回查；旧 `FAISSStore`/`BM25Store` 保留兼容 |
| `offline_core.retriever` | `HybridMethod`：dense/sparse 并行检索 + 手动 RRF 融合；策略层 `SimpleStrategy` / `FilterStrategy` / `HierarchicalStrategy`（document 摘要层粗筛→chunk 细搜）/ `ParentChildStrategy`（child 召回→parent 回查，parent 继承 child 最高分） |
| `offline_core.pipeline` | 文件夹扫描→解析（md/txt/pdf/docx，docx 先探测法律文书）→分块→（可选 enrich）→嵌入→Qdrant upsert→文档摘要点→KB 词表导出→chunks.html 导出→文档 JSON 保存 |
| `offline_core.incremental_indexer` | 增量更新：scan→apply（删除按 doc_id，新增/修改重嵌）→BM25 dirty 比例检测；`rebuild_bm25()` 全量重算 sparse；`check_and_rebuild_bm25()` 在搜索路径中触发延迟重算 |
| `offline_core.multi_model_retriever` | 3 个 embedding 模型 dense 多路 + BM25 RRF 融合（供 QA 打标使用，避免单一检索器污染标签） |
| `online_core.legal_pre_filter` | 300+ 法律关键词 + 20+ 正则 + 非法律语境排除 + KB 词表重叠率；加权评分决策（空/闲聊/无意义/非法律拦截） |
| `online_core.planner_estimator` | 21 维统计特征 + 两个 Logistic 分类器（A: 高收益类型，B: 正收益）加载 `feature_scaler.pkl`；策略 A/B/A∪B/A∩B；异常时保守放行 |
| `online_core.query_router_v2` | PlannerLLM：预算意识 Prompt（SSQ top_k=20，flat 预算 30）、难度判定、subquery/subsubquery 输出 JSON、重试+fallback LLM |
| `online_core.strategy_dispatcher` | 策略映射 + 每 SSQ 最小 20 预算 + 线程池并行执行 + Collector 按 subquery 分组去重 |
| `online_core.reranker` | `CrossEncoderReranker`（bge-reranker-v2-m3），FP16 可选，按难度保留 top-5/8/10；含 FP16/FP32 benchmark |
| `online_core.engine` | `OnlineEngine`：完整链路编排；`trace()` 运行检索 trace（不生成 LLM）供前端观测；`process()`/`process_stream()` 含三层短路与直接回复 |
| `online_core.trace_store` | PipelineTrace JSONL 持久化 + deque 最近 200 条缓存 + threading.Lock |
| `evaluation.*` | Click CLI（init/list/show/config/run/report/table/dashboard）、可组合阶段式评估管线、SQLite 实验注册表、检索缓存、指标计算（Recall/Precision/F1/AP/MRR/NDCG/HitRate）、Markdown/JSON/CSV 报告、TopK/分组分析 |
| `QA_synthesis.*` | 文档级 query 生成→语义去重（BGE+FAISS+MiniBatchKMeans 聚类）→LLM 打标（support/related/irrelevant）→质量校验；batch_client 支持百炼 Batch File API；checkpoint 断点续跑；monitor 统计耗时/token/成本 |
| `interface.api_routes` | JSON API：health、settings、kbs、indices、documents、chunks、preset 展开、trace/run、traces、qa datasets/query/trace、pipeline traces |
| `utils.*` | Token 估算（字符/DSTokenizer）、LLM 调用计费追踪、监控 |

---

## 5. RAG 管线实现细节

### 数据源与爬虫/增量更新
- 源数据为外部目录 `D:\github-repo\法律数据库爬虫\laws_files\法律`，**爬虫本身不在本项目内**；本项目只读取 `.docx` 法规文件（471 份）。
- `offline_core.incremental_indexer.IncrementalIndexer` 提供增量更新：`scan()` 基于文件大小/哈希比对 manifest，`apply()` 支持新增/修改/删除；删除和修改按 `doc_id` 删旧索引。
- BM25 稀疏向量采用“懒重建”：变更比例 >10% 时置 `bm25.dirty`，在搜索路径加载时触发 `rebuild_bm25()`（scroll 全部文本→重拟合 jieba IDF→批量更新 sparse 向量）。
- 注意：现有 `data/indices/法律/manifest.json` 中 `files` 字段为空 `{}`，说明当前索引不是通过 IncrementalIndexer 生成的，增量更新能力尚不能直接基于现有索引使用（需先全量重建入库）。

### 分块策略
- **默认结构化递归分块**（`StructureAwareChunker`，max_chars=1000）：
  - 基于 `heading_path` 维护标题栈，chunk 继承当前章节路径。
  - 法律文档“第X条”识别为原子单元（`article`），同 chunk 可含多个原子单元但不会切碎单条。
  - Code/Table/Image 独立成 chunk 并带类型元数据。
- **Parent-Child 分块**（`ParentChildChunker`，默认关闭，`PipelineConfig.enable_parent_child` 开关）：parent 1000/200，child 250/50，child 精确匹配、parent 提供上下文；chunk 携带 `chunk_level`、`parent_chunk_id`、`child_chunk_ids`、`prev/next_chunk_id`。
- **元数据**：`MetadataPipeline` 包含 Source/Structure/Keyword/Language/CsvMetadataEnricher（默认离线 pipeline 未开启 enrich）；`_extract_bbbs` 从文件名提取 32 位 bbbs 标识用于 CSV 关联。

### 索引构建（Qdrant）
- `scripts/build_index.py`：单模型索引，输出 `data/indices/<kb>/qdrant/`；`scripts/build_multi_index.py`：三模型（bge-base-zh、bge-m3、Qwen3-Embedding-0.6B）各自建 `qdrant_<model>/`，BM25 只随第一个模型构建。
- `QdrantStore` 嵌入式模式，单 collection 同时配置 dense 向量 + sparse 向量；dense on_disk，可量化，20k 以上自动 HNSW；payload 含 chunk 全字段（chunk_id/doc_id/text/metadata/heading_path/chunk_level/parent_chunk_id 等）；payload index 建立在 doc_id、chunk_level、metadata.doc_type、department、valid_status。
- 索引元数据写入 `data/indices/法律/manifest.json`（当前记录：471 docs、7,339 chunks、vocab 22,132、多模型维度 768/1024）。

### 检索
- **BM25**：`BM25Encoder`（jieba 分词 + BM25 IDF/权重）编码为 Qdrant SparseVector；支持 `tokenize_with_weights` 诊断（idf/OOV/bm25_score）。
- **Dense**：HuggingFace Embedding（默认 bge-base-zh 768d，另有 bge-m3 1024d、Qwen3 1024d）。
- **Hybrid**：`HybridMethod` 并行跑 dense+sparse 后手动 RRF 融合（k=60）；`QdrantStore.search` 也支持原生 FusionQuery RRF/DBSF。
- **Rerank**：`CrossEncoderReranker` 用 bge-reranker-v2-m3 逐对打分，替换检索分后排序；难度→保留数：simple 5 / medium 8 / hard 10。
- **过滤**：`FilterStrategy` 通过 Qdrant Filter 实现元数据过滤；`HierarchicalStrategy` 在 document 摘要点中粗筛 doc_id 再细搜 chunk；`ParentChildStrategy` 在 child 空间召回（child_top_k=max(top_k*2,50)）后回查 parent。
- **路由/预算**：PlannerLLM 输出 subquery→subsubquery；Dispatcher 保证每 SSQ 至少 20 条，最终 flat 按全局预算截断。

### 生成与问答
- `ContextManager` 组装 system prompt（`prompts/agent_prompt.txt`）+ 记忆窗口（max_messages 20）+ RAG 上下文；超阈值触发历史压缩。
- `OpenAI_LLM`（DeepSeek 兼容）支持同步/流式/异步流式；CostTracker 记录 usage。
- 非法律/闲聊 query 被 PreFilter 拦截后走 `_direct_reply`，零 RAG 开销。

---

## 6. 评估体系

### QA 数据集规模与格式
- 位置：`QA_dataset/法律/`，由 `run_qa_law.py` 生成（2026-06-25）。
- `queries.json`：2,254 条，字段 `query_id/query/query_type/difficulty/source_doc_id`；难度分布 simple 663 / medium 1102 / hard 489；类型覆盖 factoid、definition、entity_attribute、relation、comparison、aggregation、constraint、procedural、summary、multi_hop。
- `qrels.json`：11,590 条，字段 `query_id/chunk_id/relevance`（1=related 6,651 条，2=support 4,939 条）；覆盖 2,253 条 query、4,929 个唯一 chunk，平均每 query 5.14 个相关 chunk。
- 另有 `data/test_10docs_output/`（10 文档小规模测试）和 `data/datasets/`（generated/deduped/labeled 中间产物）。

### 评估脚本
- `python scripts/rag-eval.py` 或 `python -m evaluation.cli`：init/list/show/config/run/report/table/dashboard 子命令。
- `run_recall_eval.py`：批量评估 dense/sparse/hybrid 的 Recall@K，并做低分查询根因分析（OOV、IDF）。
- `verify_hybrid_improvement.py`：单点验证 hybrid 对 sparse 的改进。
- 实验矩阵位于 `experiments/*/experiment.yaml`（topk 系列、hybrid-baseline、hybrid-router-v2、online-pipeline）。

### 已跑实验与指标结果（来自 `experiments/analysis/analysis_report.md`）

**数据集 Oracle 上限**：K=5 平均 Recall 87.1%；K=10 99.0%；K=20 100%。

**TopK 实验（recall top_k=50 截断评估）关键结果**：

| 方法 | Recall@10 | Recall@20 | Recall@50 | MRR |
|---|---|---|---|---|
| **Hybrid (bge-base+BM25)** | **0.823** | **0.944** | **0.986** | **0.935** |
| Dense (bge-M3) | 0.801 | 0.915 | 0.959 | 0.928 |
| Dense (Qwen3-0.6B) | 0.787 | 0.904 | 0.955 | 0.922 |
| BM25 | 0.769 | 0.843 | 0.862 | 0.914 |
| Dense (bge-base) | 0.719 | 0.855 | 0.880 | 0.846 |
| Hybrid+Router | 0.745 | 0.762 | 0.762 | 0.895 |

**主要结论**：
1. Recall@20 是 elbow 饱和点；有 reranker 时 recall 可放大到 30-50 作候选池。
2. Hybrid+Router 曾为负优化：平均返回 8.9 条 vs Hybrid 40.8 条，延迟 5.68s vs 1.35s；根因是旧预算切分（50//N）和 Prompt 过度鼓励分解。
3. 组件消融：dense→hybrid 是唯一显著增益来源（ΔRecall@10 +0.106）；top_k=10 下 Router/Reranker 无可测增益。
4. 生产推荐配置：prefilter 开，router 关，recall hybrid top_k=20，rerank 可选（配更大 recall）。

**LegalPreFilter 实验**（`experiments/filter_optimization/`，530 条标注 query）：
- 最终 V2+ 推荐 `kw=0.70, total_thresh=0.20`：Precision=0.881，Recall=0.967，F1=0.922，ACC=0.962。

**PlannerEstimator 实验**（`experiments/planner-utility-estimator/`，2253 query）：
- 连续回归（21 维统计特征或 BGE embedding）全部 test R²<0，不可行。
- 二分类方案可行：classifier_type AUC=0.88；classifier_gain AUC=0.719；A∪B 在激活率 46.7% 下捕获 95.8% 的 Always-on 收益，节省约 53% Planner 调用。
- 高收益 query_type：comparison（41.8% gain）、multi_hop（32.5%）；低收益：factoid（15.0%）、entity_attribute（9.5%）。

---

## 7. 数据资产

### 代码/数据总体积
| 目录 | 体积 | 说明 |
|---|---|---|
| `local_model/` | 9.1G | bge-reranker-base 3.2G、bge-reranker-v2-m3 2.2G、bge-m3 2.2G、Qwen3-Embedding-0.6B 1.2G、bge-base-zh 391M、bge-small-zh 92M |
| `experiments/` | 1.5G | 各实验 results/cache（online-pipeline 264M、topk-dense-bge-m3 241M、hybrid-router-v2 202M 等） |
| `data/` | 534M | 索引 + 数据集 + traces + 日志 |
| `logs/` | 94M | qa_synthesis 主日志 |
| `QA_dataset/` | 2.4M | 法律 QA 数据集 |
| `openspec/` | 859K | 变更规范 |
| `docs/` | 276K | 设计/使用文档 |

### 索引资产（`data/indices/法律/`）
| 路径 | 体积 | 内容 |
|---|---|---|
| `qdrant/` | 179M | bge-base-zh dense + BM25 sparse（hybrid 用，collection=chunks） |
| `qdrant_bge-base-zh/` | 81M | bge-base-zh 纯 dense |
| `qdrant_bge-m3/` | 96M | bge-m3 dense（1024d） |
| `qdrant_qwen3-emb-0.6b/` | 96M | Qwen3-Embedding-0.6B dense（1024d） |
| `chunks.html` | 24M | 全量 chunk 交互浏览页 |
| `kb_vocab.json` | 256K | KB 核心词表（22,132 词） |
| `manifest.json` | 1K | 471 docs / 7,339 chunks / 多模型索引元数据 |

### 数据集/追踪
- `QA_dataset/法律/queries.json`（801K）+ `qrels.json`（1.6M）+ `quality_report_20260625_073327.json`（26K）。
- `data/datasets/`：generated_queries.json（140K）、deduped_queries.json（19K）、labeled_records.json（815K）。
- `data/traces/`：100 个 trace JSON 明细 + 3 个 JSONL 索引（2026-07-23/25），共约 9.5M。
- `data/qa-reports/`：monitor 历史与 quality_report 多版本（220K）。
- `data/laws_token_estimate.csv`（46K）、`data/legal_vocab_raw.txt`（96K）。
- `experiments/planner-utility-estimator/models/`：`classifier_type_Logistic.pkl`、`classifier_gain_Logistic.pkl`、`feature_scaler.pkl` 等（OnlineEngine 直接加载）。
- `experiments/data/legal_dict.txt`（1.1M，jieba 法律词典，LegalPreFilter 加载）。

### 模型文件
- 全部本地化于 `local_model/`，`scripts/download_models.py` 可下载 bge-m3 与 Qwen3-Embedding-0.6B；`scripts/verify_new_models.py` 验证完整性。

---

## 8. 当前状态与问题

### 可直接使用
- Qdrant 索引 `data/indices/法律/qdrant`（hybrid 用，dense+sparse）与 QA 数据集 `QA_dataset/法律`。
- `offline_core` + `online_core` 核心代码（引擎、检索策略、reranker、prefilter、planner estimator）。
- `evaluation` 评估框架与 `experiments/analysis/analysis_report.md` 结论。
- `interface` Web/API（FastAPI + React SPA + /legacy 旧 UI）。
- 本地模型 9.1G 已就绪。

### 实验半成品/需甄别
- `run_experiment.py` 仍走旧 FAISS 路径（`FAISSStore` + `DenseRetriever`），与已迁移 Qdrant 的主线脱节。
- `run_recall_eval.py` 与 `verify_hybrid_improvement.py` 中给 `SearchQuery` 传了 `query_id` 参数，但 `SearchQuery` 无此字段，**当前直接运行会 TypeError**。
- `docs/usage/OFFLINE_USAGE.md` 与 `config/default.yaml` 部分内容停留在 FAISS/Chroma 时期（`vector_db.path: ./index_store`），实际构建输出在 `data/indices/<kb>/`。
- 根目录 `index_store/` 为空壳（仅 meta.json），实际索引在 `data/indices/法律/`。

### 已知坑
1. **PreFilter 默认词表路径不一致**：`OnlineEngine`/`config/default.yaml` 默认 `index_store/kb_vocab.json`，但实际词表在 `data/indices/法律/kb_vocab.json`；不显式传参会加载空词表，KB 重叠率失效（法律关键词/正则仍工作）。
2. **Manifest 文件跟踪为空**：现有 `data/indices/法律/manifest.json` 的 `files: {}`，增量索引器无法基于它做增量更新；需通过 `IncrementalIndexer.rebuild_full()` 重建才能启用增量能力。
3. **Router 全量激活是负优化**：必须走 PlannerEstimator 选择性激活（模型文件已就绪）或直接关闭 router；否则 Recall 天花板锁定在 ~0.76。
4. **检索评估与打标存在同模型污染风险**：旧 `run_experiment.py` 用单一 bge-base 召回打标；新方案 `MultiModelRetriever` + 多模型索引已实现，但 `QA_dataset/法律` 是旧单模型产物，评估时需注意该偏置。
5. `.env` 包含多组明文 API Key（DeepSeek/阿里/OpenAI/Gemini 等），已被 .gitignore 排除；不要复制或提交到任何仓库。
6. `SearchQuery` 原生 hybrid 走 Qdrant FusionQuery RRF，而 `HybridMethod` 自己并行 dense+sparse 后手动 RRF；两条 hybrid 路径并存，行为略有差异。

---

## 9. 与法律助手 demo 的关联

### 可直接复用的模块/脚本/数据
1. **索引与检索层**：`data/indices/法律/qdrant`（hybrid 索引）+ `offline_core.store.QdrantStore` + `offline_core.retriever.HybridMethod/SimpleStrategy`。demo 只需加载现有索引即可获得 Recall@10≈0.82 的检索能力。
2. **精排**：`online_core.reranker.CrossEncoderReranker` + `local_model/bge-reranker-v2-m3`（已本地化，可 FP16）。
3. **法律领域过滤**：`online_core.legal_pre_filter.LegalPreFilter`（F1=0.922）+ `experiments/data/legal_dict.txt`；注意传入正确的 `kb_vocab_path`。
4. **问答引擎**：`online_core.engine.OnlineEngine`（三层闸门 + 检索 + 精排 + 生成）+ `online_core.query_router_v2` + `online_core.planner_estimator`（模型已训练）。
5. **评估与数据集**：`QA_dataset/法律`（2,254 query / 11,590 qrels）可直接作为 demo 检索质量回归测试集；`evaluation` 框架可继续复用。
6. **离线索引构建/更新**：`offline_core.pipeline.Pipeline`、`scripts/build_index.py`、`offline_core.incremental_indexer.IncrementalIndexer`（增量更新能力完整，但需重建 manifest）。
7. **QA 合成**：`QA_synthesis` 管线（含 batch、checkpoint、monitor）可为新知识库生成评估集。
8. **可观测 API/前端**：`interface.api_routes` 的 `/api/trace/run`、`/api/qa/*`、`/api/kbs` 与 React 页面（TraceWorkbench/QAAnalyzer）可参考或抽取。

### 需要改造/注意的地方
1. **路径配置统一**：demo 若用现有索引，应显式配置 `index_store_dir=data/indices`、`prefilter.legal_filter.kb_vocab_path=data/indices/<kb>/kb_vocab.json`，不要依赖代码默认值。
2. **SearchQuery 兼容 bug**：运行 `run_recall_eval.py` / `verify_hybrid_improvement.py` 前需删除 `query_id=` 传参，或给 `SearchQuery` 增加可选 `query_id` 字段。
3. **在线引擎依赖**：`OnlineEngine` 初始化会尝试加载 reranker 与 PlannerEstimator 模型，若 demo 环境无 `local_model/` 或 `experiments/planner-utility-estimator/models/`，需在 config 关闭 rerank/estimator 或打包对应模型文件。
4. **生成层模型**：demo 的 LLM 配置需对齐 `.env` 中的 DeepSeek/阿里 key 与 base_url；`ContextManager` 默认读 `prompts/agent_prompt.txt`，可替换为法律助手专用 system prompt。
5. **Router 策略**：demo 初期建议按实验结论关闭 router 全量激活（启用 PlannerEstimator 或直接 hybrid+rerank）；若要改进 router，优先参考 `analysis_report.md` 的修复路径（SSQ 预算、Prompt 约束）。
6. **多模型索引**：`qdrant_bge-m3`、`qdrant_qwen3-emb-0.6b` 目前为纯 dense（meta 中 sparse_vectors=null），如需多模型 hybrid，需补建 sparse 或统一走 `qdrant/`（bge-base+BM25）。
7. **增量更新落地**：现有 manifest 为空文件映射，启用增量更新前需用 `IncrementalIndexer.rebuild_full()` 重建一次。
