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

### W2 关闭 - 检索服务适配（2026-08-25）
- **完成**：chunker_v2（D01 目标策略）、query_parser、difficulty、lexicon_service、retrieval_service、rebuild_index_v2（GPU）、citation_checker（部分 W3）
- **索引重建**：451 份法规 → 4,052 parents / 18,058 children / 22,110 向量（Qdrant embedded，221MB）
- **测试**：单元测试 14 项 + 集成测试 3 项全部通过（精确法条号、语义检索、引用校验）
- **评估口径变更**：旧 qrels 跳过，M0 改人工抽检；新 qrels 由用户后续重建
- **经验**：① 长任务一律 nohup 后台 + 日志轮询，前台跑会超时；② 脚本加 `python -u` 保证日志实时；③ 嵌入 GPU batch 64 比 16 快数倍


## 开发进度快照

- W1：✅ 完成（见 CHANGELOG）
- W2：进行中——重建索引第三次（修复 article_no）运行中
