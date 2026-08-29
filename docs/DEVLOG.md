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

8. **antd Radio/Checkbox.Group options 必须用 `{value,label}`**：写成 `{key,label}` 后 option.value 全是 undefined，antd 内部对 option 值 `toString()` 抛 `Cannot read properties of undefined`，React 渲染崩溃 → 整个 tab 白屏；同时控制台报同一 key 警告。排查方法：Playwright 打开页面点对应 tab，捕获 `pageerror` 事件。
9. **`npm run build` 通过 ≠ 页面能渲染**：vite build 只做语法/打包检查，`setRuleUploading` 未定义这类 ReferenceError 只在函数调用时才触发，渲染期错误更是必须用浏览器/Playwright 实测。凡新页面都要用 Playwright 点一遍 tab、点主要按钮、看 `pageerror`。
10. **bash heredoc 长命令会被截断**（约 8-9KB）：大文件写入时分段 `cat >>` 追加；写入 Python 源码文件时，Python 字符串里的 `
` 容易在 heredoc/JSON 转义链路中被解释成真实换行，破坏生成代码。生成含换行字符串的代码时用 `chr(10)` 代替 `
`。
11. **合同还原版必须放在 agent 工作区之外**：还原产物若写入 `data/agent_workspace/contract-{cid}/`，ReAct agent 可通过 read_contract 读到还原后的原始信息，等于绕过脱敏。还原版目录应为 `data/contracts/restored/`。
12. **删除单个合同的状态文件不能 `rmtree(parent)`**：`mask_state_path(cid).parent` 是整个 `mask_state` 目录，rmtree 会误删所有合同的状态；应只 `unlink` 单个 cid 的状态文件。
13. **ReAct 合同审查 agent 必须用事件回调流式输出**：无 event_cb 时页面只显示 loading，用户以为卡死；BaseReActAgent 已支持 event_cb，assistant/contract-chat 均通过 asyncio.Queue 转发 SSE。

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

## M1 进展（2026-08-26，AFK 自动开发）
- 完成：会话管理（CRUD+消息持久化+历史注入）、上下文压缩（200k 阈值+保留5条）、知识库管理前端、合同审查规则库模板
- 测试：33/33 通过
- 阻塞：W8 规则库需法务朋友访谈填写；W10 评测需 20 份脱敏合同；W9 联网搜索依赖 agent reach skill

## 开发进度快照（2026-08-26 04:30）

- M0：代码开发关闭（W1-W6，全量测试 34/34）
- M1：可自动部分完成（会话管理/上下文压缩/知识库前端/规则库模板/search_multi/会话前端）
- M2：准备（PreFilter 评测 40 条 100%、Router 分析框架、评估报告脚本）
- 阻塞项（待用户/法务朋友）：规则库访谈、20 份评测合同、联网搜索 skill、真实问题集、安装包/虚拟机验收


- W1：✅ 完成（见 CHANGELOG）
- W2：进行中——重建索引第三次（修复 article_no）运行中

## 迭代需求处理（2026-08-27 试用反馈）

- 处理 `docs/草稿/迭代需求.md` 8 条：硬件适配（embedding CPU / reranker skip|local|api）、启动预加载、Qdrant AlreadyLocked 根治（chunk_api/citation_checker 全走单例）、免责声明去重、PreFilter 评估 40/40、chunker_v2 审查、chunk 全文预览修复、知识库多选/文件夹上传
- 检索链路评估：新 qrels 经 remap 后（`data/QA_dataset/法律/qrels_v2.json`，chunker_v2 child 级）评估 2,218 条 query
  - 固定 K=10：Hit 0.9743 / MRR 0.9209 / Recall 0.8046 / NDCG 0.8265
  - 链路最终（难度自适应截断）：Recall@10 0.8209 / NDCG@10 0.8636
  - 分难度：hard/medium 表现接近，simple 组 recall 偏低（qrels 多标签分母大）
- 全量测试：39 passed
- 经验：① FastAPI 路由 `/locate` 必须注册在 `/{chunk_id}` 之前，否则被动态路由吞掉；② CitationChecker 与 chunk_api 一样不能自己 new QdrantClient；③ LLM 会从历史里学免责声明，后端追加前先 strip 一次

## 合同审查 agent v1（2026-08-28，AFK 自动开发）
- 选型：vendored LegalMask（MIT）作为可逆脱敏引擎；PrivacyGuard(lizilaywer) 与 before-signing 作为后续 OCR/工作台参考
- 完成：合同上传（多文件 docx/pdf）→ 原件存 raw（agent 不可访问）→ LegalMask 脱敏 → 脱敏版入 agent 工作区 → mapping 存不可访问目录 → 规则审查（内置 6 条 + 用户 rules.jsonl 上传）→ 报告/脱敏版/还原版下载 → 删除
- 前端新增「合同审查」tab
- 测试：65 passed
- 待办：LLM 审查工作流、完整 SKILL.md 用户 skill、修订版合同、在线编辑、扫描 PDF OCR


## 合同审查 D11（2026-08-29，AFK 自动开发）
- 完成：可配置脱敏（人名/企业名/信用代码/电话/邮箱/身份证号 × 中间打码/占位符/哈希）、扫描清单、脱密映射配置与按选中配置还原、拖选原文片段加入清单、新版三栏 UI（左文档列表可收起 / 中预览+版本切换 / 右脱敏+审查+文档产物 tab）、上传文件/文件夹、重命名/删除、规则库 txt/md/jsonl 多选上传、ReAct 合同审查 agent chat（自动附带当前脱敏文件与规则库引用，生成批注 edit 版 + Markdown 报告 + 批注 DOCX）
- 测试：68 passed（新增 test_configurable_mask_flow）
- 白屏修复：Radio/Checkbox options 的 value 缺失导致 antd toString 崩溃，已用 Playwright 回归验证 0 pageerror
- 待办：扫描 PDF OCR、人名/企业名识别改用 NER 或引入法学专名库、批注版在线编辑、律师朋友验收内置规则
