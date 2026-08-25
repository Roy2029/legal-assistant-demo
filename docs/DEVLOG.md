# 开发日志（DEVLOG）

> 记录开发进展、待决事项、踩坑记录。AFK 期间由 agent 维护。

## 待开发清单（用户 2026-08-26 反馈新增）

1. **Trace 面板：混合召回中间结果可见**——分别展示 dense / BM25 各自召回的 chunk 列表（chunk_id、得分、文本摘要，可折叠展开全文），用于判断语义检索质量；
2. **Trace 面板：BM25 查询分词可见**——展示 BM25 对 query 的分词结果，并标记命中的用户自定义词典词，用于判断自定义关键词是否生效。

## 待人工决断事项（回来处理）

0. **配置 LLM API Key**：启动 demo 后在设置页填 Base URL/API Key/Model（OpenAI 兼容），问答功能即可用；否则 /api/chat 返回明确错误提示。


1. **验证 M0 后找律师朋友试玩**：需要你约法务朋友/律师做 demo 体验（M0 退出准则依赖）。
2. **20 份脱敏合同**：M1 评测集需要向法务朋友索取（W10 前）。
3. **离线案例数据**：指导性案例/公报案例/脱敏判决书（W5 前）。
4. **README 仓库可见性**：已按 private 创建 GitHub 仓库，如需 public 请改。
5. **transformers 全局降级影响**：全局 Anaconda 已降级 transformers 4.57.6；surya-ocr 等依赖 transformers>=5 的项目会受影响。已建 .venv 隔离，但全局未恢复。建议确认是否恢复全局 transformers 5.x（demo 用 .venv 不受影响）。

## 踩坑记录

7. **reranker 在 GTX 1650 上的三重坑**：① `.half()` FP16 转换后 predict 极慢（580s/批）；② FP32 模型 2166MB + 系统占用导致 4GB 显存实际可用仅 ~1.2GB，GPU 放不下；③ CPU rerank 长文本 30 对远超短文本 benchmark。M0 关闭 rerank，M2 再优化（量化/候选裁剪/换机）。

1. **transformers 5.x 与 sentence-transformers 5.5 不兼容** → 降级 transformers<5；demo 用 .venv 隔离。
2. **Qdrant 本地嵌入式不支持 payload index** → 仅 server 模式生效；交付版用 server 子进程。
3. **chunker 递归切分死循环** → _split_by_candidates 切不动时需直接硬切 + 递归深度保护。
4. **article_no 提取层级错误** → 文档级第一条会污染全部 chunk；必须 chunk 级提取首个"第X条"。
5. **旧 qrels 与新 chunker 不对齐** → 旧 qrels 聚合为 doc-level 评估（doc_id 新旧一致）；chunk-level 重标注列 M2。
6. **Windows Git Bash 的 /d/ 路径**：str_replace_editor 会把 /d/ 解析到 C:\d，写文件用 bash 或 D:/ 路径。

## 阶段简报

### W6 关闭 - 打磨与交付（2026-08-26，代码部分）
- 完成：可逆脱敏（desensitize + restore）、update 占位 API、demo 演示脚本、全量测试 31/31
- 人工验收待办：Inno Setup 安装包制作、全新 Windows 虚拟机验收、律师朋友体验
- 经验：① 脱敏映射表是最高敏感资产，Fernet 加密 + 密钥分离；② 全量 pytest 中 Qdrant 本地嵌入式多实例会锁冲突，测试必须复用单例或先 close


### W5 关闭 - 实务助手框架（2026-08-26）
- 完成：skill 注册表（SKILL.md frontmatter 解析）、4 个业务动作桩、/api/assistant SSE 工具调度、kb_retrieval/case_retrieval 工具
- 测试：3/3 通过（actions/assistant_stream/unknown_action）
- 经验：① get_retrieval_service() 默认配置必须指向 demo 新索引（曾指向 RAG1.0 旧索引导致 law_name 空）；② M0 案例检索返回空态提示，绝不编造案例


### W4 关闭 - 用户知识库（2026-08-26）
- 完成：上传/列表/删除 API、md/txt/docx 解析入库、corpus=user + user_id 元数据隔离、检索 corpus_scope、复用单例 QdrantStore 避免锁冲突
- 测试：pytest 1 passed（上传→列表→scope=user 检索命中→删除→不命中）；txt/docx 实测上传成功
- 经验：① 测试内必须复用 get_retrieval_service() 单例，新建 QdrantStore 会锁冲突；② 上传大文件首次加载 embedding 模型约 30s，curl 测试要放宽超时


### W3 关闭 - 知识库问答闭环（2026-08-26）
- 完成：SSE /api/chat、引用校验器（打回1次+追加提示）、PreFilter 保守模式、LLM 客户端（.env）、React 前端（问答流式 + Trace 双视图 + dense/BM25 中间 chunk + BM25 分词 + 设置页 + 词典管理）、一键启动脚本
- 测试：19/19 单元测试通过；真实 LLM 链路验证通过（民法典第32/580条）
- 经验：① Trace 中间结果（dense/BM25 分路）对判断检索质量至关重要；② exact_match 查询按目标条文截取上下文片段，避免 LLM 在大块中找不到目标条文；③ GTX 1650 显存不足，M0 关闭 rerank


### W2 关闭 - 检索服务适配（2026-08-25）
- **完成**：chunker_v2（D01 目标策略）、query_parser、difficulty、lexicon_service、retrieval_service、rebuild_index_v2（GPU）、citation_checker（部分 W3）
- **索引重建**：451 份法规 → 4,052 parents / 18,058 children / 22,110 向量（Qdrant embedded，221MB）
- **测试**：单元测试 14 项 + 集成测试 3 项全部通过（精确法条号、语义检索、引用校验）
- **评估口径变更**：旧 qrels 跳过，M0 改人工抽检；新 qrels 由用户后续重建
- **经验**：① 长任务一律 nohup 后台 + 日志轮询，前台跑会超时；② 脚本加 `python -u` 保证日志实时；③ 嵌入 GPU batch 64 比 16 快数倍


## M0 总简报（2026-08-26）

- **代码开发关闭**：W1-W6 全部完成，全量测试 31/31 + kb_api 1/1 通过
- **核心能力**：知识库问答（精确法条号/语义检索/引用校验/PreFilter/Trace 双视图）、用户知识库（上传/隔离/删除）、实务助手框架（skill 注册表 + 工具调度 + 4 动作桩）、可逆脱敏、一键启动
- **待人工验收**：Inno Setup 安装包、全新 Windows 虚拟机验收、律师朋友体验（M0 退出准则）
- **M1 启动条件**：M0 人工验收通过后正式启动；代码侧可先推进会话管理/上下文压缩

## 开发进度快照

- W1：✅ 完成（见 CHANGELOG）
- W2：进行中——重建索引第三次（修复 article_no）运行中
