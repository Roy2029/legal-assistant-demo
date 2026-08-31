# Changelog

## v0.3.1（2026-08-31）- WenshuMCP 随包分发 + per-user 安装

- **WenshuMCP 内置**：`vendor/wenshumcp/`（wenshu_mcp + wenshu_api，1.5M 纯 Python）随包分发，Case Agent 不再依赖外部工作副本；项目位置解析顺序 `WENSHU_MCP_PROJECT` → vendor → 开发机副本（`online_core/mcp/wenshu_adapter.py`），初始化日志带 vendor 指纹
- **版本追踪**：`packaging/vendor_wenshumcp.py` 一键同步（源目录非 git 仓库，以包内容 sha256 指纹为准，元文件不参与）；`VENDOR.json` 记录来源与指纹
- **page_id 轮换对策**：`WENSHU_ALGO_CONFIG` 环境变量可指向替换文件（优先级高于出厂值），站点轮换无需重装/重打包
- **安装形态**：per-user 安装至 `{localappdata}\LegalAssistantDemo`（`PrivilegesRequired=lowest`）——主应用 PROJECT_ROOT 相对定位要求安装目录整体可写，放弃 Program Files；会话快照落 `~/.wenshu/`（天然可写、重装不丢）
- `packaging/output/` 入 .gitignore；0.2.0 exe（247MB LFS）移出版本控制

## 2026-08-31 - Case Agent 接入 WenshuMCP 新版 MCP

- 适配器（`online_core/mcp/wenshu_adapter.py`）：项目路径迁移 `D:/个人开发/裁判文书检索MCP` → `C:/Users/Roy/WorkBuddy/WenshuMCP`（可 `WENSHU_MCP_PROJECT`/`WENSHU_MCP_PYTHON` 覆盖）；MCP 子进程默认解释器改用 WenshuMCP 官方托管环境 `~/.workbuddy/binaries/python/envs/default`（缺失回退当前解释器）；入口改 `python -m wenshu_mcp.server`；支持向 MCP 子进程注入环境变量（凭据经 `WenshuMCPConfig.env`，来源设置页加密配置，不落盘）；解析 MCP 统一 JSON 返回 `{ok, error_code, data|message}` 并透传 `error_code`/`data`
- 修复 WenshuMCP 上游缺陷并解决 0 命中（`C:\Users\Roy\WorkBuddy\WenshuMCP`）：① `health_check` 用 `sync_playwright()` 探测 chromium 在 server 进程死锁——改为纯文件系统扫描，模块名 `pycryptodome` 修正为 `Crypto`；② ddddocr 运行中首次导入病态卡死（20s~150s+）——server 启动期预导入；③ 全部 8 工具改 `async def` + 单线程执行器委托（根因：mcp ≥1.2x FastMCP 在事件循环线程直接执行同步工具，sync_playwright 报 `Sync API inside asyncio loop`）；④ **检索 0 命中根因**：`wenshu_api/algo_config.json` 的 `page_id` 为旧值（站点发布已轮换），经「驱动真实 UI 抓包对比」定位并更新为 `3a8d444325cd591da840af0f701b52fa`——e2e 与 CaseAgent 全链路验证 `total=14,065,609`、全文/下载通过；另确认 **pageSize 仅接受 5/10/20**，非法值静默返回 0
- CaseAgent 工具描述标注 `page_size` 仅 5/10/20；prompt 补充 0 命中软拦截处理指引
- CaseAgent 工具面对齐新版 MCP：`case_search`→`advanced_search`（keyword 必填，日期区间过滤暂不支持，sort=s50:desc 近似）、`case_read`→`get_document`（按 doc_id 读取，默认 text 省上下文）、新增 `case_status`/`case_login`（站点验证码已改「点选文字」型，CAPTCHA_FAILED/SESSION_EXPIRED 时人工浏览器点选）；移除新版已不存在的 `case_search_by_law`/`case_guided`（按法条检索改为 keyword 方式）；system prompt 同步错误码处理策略
- `assistant_api.tool_case_retrieval`：改用新签名并真正解析 `data.items`（旧实现 total 恒为 0），超时 30s→60s（浏览器后端更慢）
- 测试/冒烟：`test_case_agent` FakeAdapter 与断言对齐新结构；`smoke_case_agent.py` 改为 health_check→session_status→advanced_search→人工登录重试流程
- 文档：`docs/INVENTORY_MCP.md` 顶部增加 2026-08-31 迁移差异说明（旧文存档）

## 2026-08-31 - Agent 记忆/Trace 绑定/UI 迭代（agent-context-trace-ux）

### Agent 能力
- 会话记忆：agent 入口（rag/case/assistant/contract chat）注入会话历史（最近 20 条，脱敏后传入），`BaseReActAgent.run()` 支持 `history` 参数；多轮追问可引用前文
- 上下文压缩阈值 200K → 128K（对齐 DeepSeek 窗口），chat 与 agent 统一
- 闲聊模式：问候/寒暄直接对话式回应，不再套「资料检索报告」模板（模板兜底仅在有工具轮次时生效）；system prompt 增加闲聊指引
- 修复：闲聊时落库内容 answer+report 重复拼接

### 前端
- Trace 会话绑定：切换会话立即清空 trace/引用/反馈并从持久化恢复；`runAgent` 支持 AbortController 中止；SSE 事件与异步加载均带会话竞态守卫
- 空 trace（无工具轮次）不再渲染空「原始 trace JSON」折叠框
- 会话列表 240→280px，重命名/删除收进 ⋯ Dropdown（hover 显示），标题可见 ≥10 汉字
- 知识库文件树修复穿模（antd Space 包裹层断开 min-width:0 链）；修复 `<Tag flexShrink>` 无效 prop；行操作 hover 显示

### 依赖
- requirements 补 `SQLAlchemy>=2.0`（顶层硬导入）；删除未使用的 streamlit/plotly/pandas；faiss/rank-bm25/mcp/scikit-learn 注释标注为可选兜底
- `offline_core/chunker.py` 错误的 `from openai import BaseModel` → pydantic（openai 不再是 live 依赖）
- 清理上轮遗留的 15 个磁盘死文件（`git rm --cached` 未删盘的 online_core/offline_core 模块）

## 2026-08-30 - P0/P1 Review 修复（fix-review-p0-p1）

### P0 安全
- 合同 API 路径穿越防护：`/api/contracts/{contract_id}` 全部改为 `^[0-9a-f]{32}$` 校验，非法值（含 `..\` 等编码）直接 422，杜绝 `rmtree` 任意目录删除
- CORS 修复：`allow_origin_regex` 替换失效的 `http://127.0.0.1:*`，dev(5173)/prod(8000) 命中、恶意 origin 拒绝
- 凭据卫生：删除 `scripts/wenshu_login_*.py`、`smoke_case_auto_login.py`（含硬编码手机号/密码），清除泄露日志
- 密钥配置安全：`GET /api/config` 只返回脱敏副本（`llm.api_key`/`wenshu.password` → `***` + `api_key_set`）；`llm.api_key` 落盘前 Fernet 加密（明文迁移自动回写，迁移前备份）；解密失败显式置空告警而非 fail-open；PUT 忽略 `***`/空值保留原 key
- 前端设置页校验放宽：空值=保留原值，占位显示「已配置」

### P1 功能
- 脱敏修复：`restore()` 去掉 `kind:` 前缀与反转 bug；占位符映射持久化、全局唯一、同原文幂等复用；脱敏覆盖 agent（RAG/案例/合同）query 入口与最终答案还原、聊天历史注入前脱敏、落库脱敏
- 多轮历史含 user 消息：`msg_kind IN ('user','final')`，user 消息进入 LLM 上下文
- 死代码删除：`online_core/`（engine/query_router/strategy_dispatcher 等 10 个）、`offline_core/`（pipeline/enricher 等 5 个）、`evaluation/`、`utils/`、`tools/`、App.jsx 死页面（约 200KB+700 行）；`scripts/start_dev.py`（与 start_prod.py 重复）
- 依赖对齐：`requirements.txt` 钉 `transformers<5`、`python-multipart>=0.0.18`（CVE-2024-53981），补 `PyMuPDF`、`pytest`、`reportlab`
- 测试隔离：`tests/conftest.py`（tmp_db/tmp_config/tmp_anon + slow marker）+ `pytest.ini`（testpaths=tests）；fast 套件 65 passed，重资产用例按 slow 分组

## [Unreleased] - M0 能跑 开发中

### M2 准备（2026-08-26）
- PreFilter 评测集 40 条（30 不应拦截 + 10 应拦截）+ 评测脚本，准确率 100%
- 修复 2 条误杀（二手房交易税费/加班费怎么计算）

### Added（M0 遗留项完成）
- 引用卡片点击定位原文：/api/chunk/locate + 前端引用标签 + Modal 原文展示

## M1 W7-W9 进展（2026-08-26）
- 会话管理前端（会话选择/新建/删除/历史加载）
- search_multi 多子查询并行检索合并
- 全量测试 33/33 + 集成 4/4 通过
- 会话管理 API（/api/sessions CRUD + 消息）
- chat 消息持久化 + 历史注入（最近 20 条）+ 上下文压缩
- 知识库管理前端页（上传/列表/删除）
- 合同审查规则库模板（skills/contract_review/rules.jsonl，待法务朋友填写）
- 全量测试 33/33 通过
- M1 剩余：法务访谈规则库（W8）、子 Agent 增强（W9）、20 份合同评测（W10-11）——依赖法务朋友/外部数据

## W6 关闭 - 打磨与交付（代码部分，2026-08-26）
- 规则版可逆脱敏（手机/身份证/银行卡/公司名 + Fernet 加密映射 + 最终输出还原）
- /api/update 占位（增量更新未接爬虫，明确状态）
- 演示脚本 scripts/demo_guide.md
- 全量测试 31/31 通过
- 人工验收待办：Inno Setup 安装包、虚拟机验收、律师体验

## W5 关闭 - 实务助手框架（2026-08-26）
- Skill 注册表：skills/*/SKILL.md（4 个业务动作：案例检索/案情分析/合同审查/法律研究备忘录）
- /api/actions + /api/assistant SSE（step_start/tool_call/tool_result/final）
- 工具调度：kb_retrieval（真实检索）+ case_retrieval（离线库空态提示，不编造）
- 修复：get_retrieval_service 默认指向 RAG1.0 旧索引导致 law_name 为空
- 测试：3/3 通过

## W4 关闭 - 用户知识库（2026-08-26）
- /api/kb/upload：md/txt/docx 解析入库（python-docx/MarkdownParser/pypdf），corpus=user 元数据隔离
- /api/kb/docs 列表 + DELETE 删除（同步删向量与文件）
- 检索隔离 corpus_scope（all/public/user）
- 测试：test_kb_api 上传→列表→隔离检索→删除通过；txt/docx 实测上传成功
- pdf 解析代码就绪（pypdf 文本层），待用户提供样例测试

## W3 关闭 - 知识库问答闭环（2026-08-26）
- PreFilter 保守模式（只拦明显闲聊，宁放勿杀）
- Trace 面板展示 dense/BM25 各自召回 chunk 列表（可折叠展开）与 BM25 查询分词
- 全部单元测试 19/19 通过
- 已知 M0 剩余：引用卡片点击定位原文（列入待开发清单）

### Fixed
- 精确法条号检索 0 召回（第32条）：根因是命中 chunk 以第13条开头、目标条文在中后部，LLM 未找到；改为 exact_match 时按目标条文截取上下文片段
- reranker 在 GTX 1650 上极慢（580s/批）：M0 默认关闭 rerank，exact_match 跳过精排；语义检索 10 分钟 → 3 秒
- 一键启动脚本闪退：移至根目录、全英文、空标题 start /B、ping 延时、pause 停留

### Added (W3)
- /api/chat SSE 流式问答（retrieval→generation→citation_check→final）
- 引用校验器（打回 1 次 + 追加未验证提示；真实法条/虚构法条测试通过）
- LLM 客户端（OpenAI 兼容，.env 读取 LLM_API_KEY/LLM_BASE_URL）
- React 前端骨架：知识库问答（流式 + Trace 面板）、设置页（LLM 热更新 + 词典管理）
- 一键启动脚本 scripts/start_all.bat / start_all.py / stop_all.bat
- 真实 LLM 链路冒烟验证通过（民法典第580条 → 回答+引用+免责声明）

## W2 关闭 - 检索服务适配（2026-08-25）
- 索引重建完成：451 份法规，4,052 parents / 18,058 children（chunker_v2 目标策略）
- 精确法条号检索验证通过（民法典第580条命中 3 条含 580 的 chunk）
- 引用校验器验证通过（真实法条 verified，虚构法条 unverifiable）
- 集成测试全部通过（tests/test_integration_core.py）
- 旧 qrels 评估跳过（新 qrels 由用户后续重建）
### Added (W1)
- 项目仓库骨架（server/ frontend/ skills/ tools/ tests/ scripts/ config/）
- 迁入 RAG1.0 代码基线（offline_core/online_core/evaluation/utils/prompts）
- FastAPI 骨架（/health /api/config）+ SQLite 11 张表
- 开发启动器 scripts/start_dev.py
- 索引 Gap 分析报告（结论：必须重建）
### Added (W2)
- chunker_v2：D01 目标分块器（节最小单位/首部保留/均分/父子索引）
- query_parser：规则+法规名词典（否定排除/多候选）
- difficulty：难度分档规则版
- lexicon_service：查询期用户词典
- retrieval_service：统一检索服务（解析→难度→词典→混合检索→rerank）
- rebuild_index_v2：GPU 重建脚本 + doc-level 评估脚本
### Fixed
- transformers 5.x 与 sentence-transformers 5.5 不兼容（降级 4.57.6）
- chunker_v2 递归死循环（深度保护+切不动硬切）
- article_no 文档级提取错误（改为 chunk 级提取）
- Qdrant filter 路径（metadata 嵌套字段）
