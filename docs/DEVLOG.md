# 开发日志（DEVLOG）

> 记录开发进展、待决事项、踩坑记录。AFK 期间由 agent 维护。

## 待人工决断事项（回来处理）

1. **验证 M0 后找律师朋友试玩**：需要你约法务朋友/律师做 demo 体验（M0 退出准则依赖）。
2. **20 份脱敏合同**：M1 评测集需要向法务朋友索取（W10 前）。
3. **离线案例数据**：指导性案例/公报案例/脱敏判决书（W5 前）。
4. **README 仓库可见性**：已按 private 创建 GitHub 仓库，如需 public 请改。
5. **transformers 全局降级影响**：全局 Anaconda 已降级 transformers 4.57.6；surya-ocr 等依赖 transformers>=5 的项目会受影响。已建 .venv 隔离，但全局未恢复。建议确认是否恢复全局 transformers 5.x（demo 用 .venv 不受影响）。

## 踩坑记录

1. **transformers 5.x 与 sentence-transformers 5.5 不兼容** → 降级 transformers<5；demo 用 .venv 隔离。
2. **Qdrant 本地嵌入式不支持 payload index** → 仅 server 模式生效；交付版用 server 子进程。
3. **chunker 递归切分死循环** → _split_by_candidates 切不动时需直接硬切 + 递归深度保护。
4. **article_no 提取层级错误** → 文档级第一条会污染全部 chunk；必须 chunk 级提取首个"第X条"。
5. **旧 qrels 与新 chunker 不对齐** → 旧 qrels 聚合为 doc-level 评估（doc_id 新旧一致）；chunk-level 重标注列 M2。
6. **Windows Git Bash 的 /d/ 路径**：str_replace_editor 会把 /d/ 解析到 C:\d，写文件用 bash 或 D:/ 路径。

## 阶段简报

（每个阶段关闭后追加）

## 开发进度快照

- W1：✅ 完成（见 CHANGELOG）
- W2：进行中——重建索引第三次（修复 article_no）运行中
