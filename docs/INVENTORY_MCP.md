# 裁判文书检索 MCP 项目索引

> 侦察目标：`D:\个人开发\裁判文书检索MCP`（bash 路径 `/d/个人开发/裁判文书检索MCP`）
> 侦察时间：2026-07（按仓库文件时间戳）
> 说明：本文只记录事实与要点，敏感值已打码。

## 1. 项目概览

- **一句话定位**：把中国裁判文书网（wenshu.court.gov.cn）的高级检索封装为 14 个 MCP 工具，供 AI Agent（Claude Code / Cursor / Cherry Studio）以自然语言检索裁判文书。
- **技术栈**：Python ≥ 3.11；`mcp[cli]`（FastMCP，STDIO 协议）；`httpx`（API 直发）；`playwright`（可见浏览器登录/抓包）；`pycryptodome`（DES3 加解密）；`ddddocr`（验证码 OCR）。
- **当前状态**：
  - 核心加密/解密、ciphertext 令牌、搜索条件编码均已逆向并验证。
  - `tools/capture_browser.py` 抓包工具 7/23 实测搜索成功（交通事故，`resultCount=6555229`，code=1）。
  - `src/wenshu_mcp/core/api_client.py` 已从“Playwright fetch”重构为“httpx 直发 + WAF 稳定 Cookie”，DEVELOPER_GUIDE 声称 code=9 已修复；但 `docs/CURRENT_STATUS.md`（7/21）仍记录该问题，两者存在时间差。
  - 自动化登录（OAuth + OCR 验证码 + WAF 等待 + Cookie 持久化）已有实现与脚本。
  - 项目整体仍在“可抓包搜索、API 客户端全流程待最终确认”的阶段。

## 2. 目录结构（2-3层，不含 .venv）

```
裁判文书检索MCP/
├── .claude/
│   ├── commands/opsx/               # openspec 命令
│   ├── settings.local.json          # Claude Code 本地权限配置
│   └── skills/
│       ├── lei-an-jian-suo/SKILL.md # 类案检索方法论 skill
│       └── openspec-*/SKILL.md      # OpenSpec 工作流 skills
├── .env                             # 登录凭据（WENSHU_USER_NAME / WENSHU_PASSWORD，已打码）
├── .mcp.json                        # MCP 客户端配置（指向 .venv python）
├── .playwright-mcp/                 # Playwright MCP 运行日志/页面快照（可忽略）
├── README.md                        # 项目说明与快速开始
├── account_center.md                # 账号中心页面可访问性树快照（677 行）
├── captcha_cache.json               # 验证码图片 MD5 -> OCR 结果缓存
├── cookies_input.json               # 早期手工 Cookie 输入样例（含旧 SESSION，敏感）
├── current_state.md / home_state.md # 页面可访问性树摘录（调试遗留）
├── debug/                           # code=9 调试目录与 WAF 拦截截图
│   ├── code9_20260721_*/            # 4 个调试输出目录
│   ├── waf_blocked.png
│   └── waf_blocked_simple.png
├── debug_waf_check.png              # WAF 检查截图
├── docs/
│   ├── CURRENT_STATUS.md            # 7/21 现状（code=9 未解决）
│   ├── DEVELOPER_GUIDE.md           # 7/23 技术架构与运维文档（较新）
│   ├── DOMAIN_MODEL.md              # 领域模型/逆向分析（部分过时）
│   ├── GUIDE.md                     # 目录导引
│   └── USER_MANUAL.md               # 法律工作者使用手册
├── judgment-detail.png              # 文书详情页截图
├── login_wenshu.py                  # 根目录版自动登录脚本（调 auto_login）
├── login_with_captcha.py            # 根目录版 Playwright+OCR 登录脚本
├── logs/
│   └── api_capture_log.txt          # 抓包日志（3974 行，7/23 搜索成功记录）
├── openspec/
│   ├── config.yaml                  # spec-driven 配置
│   ├── changes/archive/             # 已归档的 code=9 调试 change
│   └── specs/                       # 3 个能力 spec（debug/e2e/对比调试）
├── pyproject.toml                   # 项目元数据与依赖
├── screenshots/                     # 18 张调试截图（登录/验证码/搜索/WAF）
├── scripts/                         # 17 个调试与验证脚本（详见 §3）
├── search-results-4447.png          # 搜索结果截图
├── src/wenshu_mcp/                  # 核心源码（详见 §4）
│   ├── __main__.py
│   ├── server.py
│   ├── config.py
│   ├── core/
│   │   ├── api_client.py
│   │   ├── playwright_manager.py
│   │   ├── crypto.py
│   │   ├── cipher.py
│   │   └── rate_limiter.py
│   ├── models/schemas.py
│   └── tools/definitions.py
├── tests/                           # 24 个测试脚本（详见 §3）
├── tools/
│   └── capture_browser.py           # 浏览器抓包分析工具
├── wenshu_cookies.json              # 持久化 Cookie（含 SESSION，敏感）
├── wenshu-home.md / wenshu-page.md  # 首页可访问性树快照
├── wenshu-login-page.md / wenshu_login.md / wenshu_filled.md
│                                    # 登录页可访问性树快照
└── 参考资料/                         # 逆向/方法论/业务资料（8 个 .md + 1 个 .txt）
```

## 3. 关键文件与文档

| 路径 | 说明 |
|------|------|
| `README.md` | 项目简介、14 工具列表、安装/使用流程、搜索字段说明 |
| `docs/DEVELOPER_GUIDE.md` | 最新技术文档：架构、httpx 直发重构、WAF Cookie 时序、错误码、运维 |
| `docs/CURRENT_STATUS.md` | 7/21 现状：已修复排序字段/响应格式/cipher，code=9 仍在排查 |
| `docs/DOMAIN_MODEL.md` | 逆向分析：导航树、API 端点、搜索字段编码、加密机制（部分过时） |
| `docs/USER_MANUAL.md` | 面向律师/法务的使用手册，含 6 个场景化工具示例 |
| `docs/GUIDE.md` | 目录导引与测试脚本选择指南 |
| `.mcp.json` | MCP 客户端配置，命令指向 `.venv\Scripts\python.exe -m wenshu_mcp` |
| `.env` | 存储 `WENSHU_USER_NAME` / `WENSHU_PASSWORD`（敏感，已打码） |
| `pyproject.toml` | 包名 `wenshu-mcp`，依赖 mcp/httpx/pycryptodome/playwright/ddddocr |
| `login_wenshu.py` | 从 .env 读凭据并调用 `auto_login()` 的入口脚本 |
| `login_with_captcha.py` | 独立 Playwright+ddddocr 登录脚本（iframe 方案，含 WAF 等待） |
| `wenshu_cookies.json` | 5 个 Cookie（SESSION/_bl_uid/ncCookie/HOLDONKEY/wzws_reurl），含登录会话 |
| `cookies_input.json` | 早期 Cookie 输入样例（含旧 SESSION 明文，有泄露风险） |
| `captcha_cache.json` | 4 条验证码图片哈希到识别结果缓存 |
| `openspec/specs/*/spec.md` | 3 个能力 spec：waf-debug-instrumentation、comparison-debug-flow、e2e-search-verification |
| `openspec/changes/archive/2026-07-21-debug-code9-waf-rejection/` | code=9 调试 change 的 proposal/tasks（任务未全部勾完） |
| `.claude/skills/lei-an-jian-suo/SKILL.md` | 类案检索方法论，明确调用本项目 wenshu MCP 工具 |
| `参考资料/*.md` | 逆向参考、类案检索业务规则、案情描述、MCP 教程等 9 份资料 |

## 4. 核心代码模块

### `src/wenshu_mcp/`（核心源码）

| 模块 | 职责 | 关键实现点 |
|------|------|-----------|
| `__main__.py` | STDIO 入口 | `mcp.run(transport="stdio")`，日志走 stderr |
| `server.py` | FastMCP 服务器 | 注册 14 个工具（含 1 个废弃 `set_csrf_token_tool`） |
| `config.py` | 全局配置 | 固定 `PAGE_ID=6f08bb13c52123bee1d0c4cc5100c94a`；`DEFAULT_SORT=s50:desc`；限速 1500ms；headless=False；DES3 固定密钥；错误码常量 |
| `core/api_client.py` | API 通信核心 | `WenshuAPIClient`；httpx 直发 `rest.q4w`；本地生成 24 位 Token；`_build_post_data` 构建 ciphertext/token/pageId/queryCondition；`_check_error` 错误码映射；`_decrypt_result` 响应解密；验证码 code=-11 指数退避重试 |
| `core/cipher.py` | ciphertext 反爬令牌 | 时间戳 + 24 位 salt + yyyyMMdd IV 做 DES3 加密，拼 `salt+iv+enc` 后逐字符转二进制 |
| `core/crypto.py` | DES3 加解密 | 3DES/CBC/PKCS7；IV 为当天 `yyyyMMdd`；短密钥补足 24 字节 |
| `core/rate_limiter.py` | 限速器 | 令牌桶式最小间隔 1500ms，±20% 抖动，统计最近 20 次请求 |
| `core/playwright_manager.py` | 浏览器自动化 | 仅用于 auto_login；OAuth 直连、验证码 OCR 重试、WAF 等待 5s + reload、Cookie 过滤 `wzws_` 前缀并持久化；兼容 `page.evaluate(fetch)` 请求方式（旧调试路径） |
| `models/schemas.py` | 搜索条件构建 | `SearchCondition`；字段编码 `s1-s47/flyj`；案件类型/法院层级/文书类型/省份字典；结果字段映射 |
| `tools/definitions.py` | MCP 工具实现 | 全局单例 `_client`；`_format_search_result` 兼容新旧格式；场景化搜索 + 登录 + 字典 + 状态工具 |

### `tools/capture_browser.py`

- 浏览器抓包分析：拦截 `rest.q4w` 请求/响应，解析 POST 参数，自动 DES3 解密，输出到 `logs/api_capture_log.txt`，并保存 Cookie。
- 7/23 日志显示：搜索“交通事故”成功，响应 dict 格式，`resultCount=6555229`，首条键为 `['44','1','2','26','7','rowkey','9','31','10','32','43']`。

### `scripts/`（调试脚本，按用途）

| 脚本 | 说明 |
|------|------|
| `quick_test.py` | 自动登录后用 requests 验证 currentUser + 搜索（全链路快速验证） |
| `e2e_verify_fix.py` | 端到端验证修复：Cookie 有效性检查 → 登录 → requests/httpx/api_client 三通道搜索 |
| `auto_login_refresh.py` | 自动登录刷新 Cookie 并验证 WAF 两步导航、currentUser、搜索 |
| `post_login_diagnose.py` | 登录后诊断：WAF 挑战流程、Token 出现时机、搜索测试 |
| `cookie_test.py` / `fetch_diag.py` / `test_httpx_cookies.py` | 诊断 Cookie 注入、浏览器 fetch 是否带 Cookie、httpx 直发是否可用 |
| `token_source.py` | 追踪 CSRF Token 来源（结论：本地生成，不在 DOM） |
| `search_mechanism.py` / `click_search.py` | 分析搜索页表单/按钮/事件触发机制 |
| `nav_debug.py` / `nav_search_test.py` / `nav_test_visible.py` / `simple_nav_test.py` | 导航与搜索页测试 |
| `network_trace.py` | 网络请求追踪 |
| `check_scripts.py` / `fresh_session_test.py` | 脚本检查 / 新会话测试 |

### `tests/`（测试脚本，按用途）

- **当前活跃**：`test_mcp_core.py`（直接 Playwright fetch，历史标注 ✅）、`test_mcp_flow.py`（API Client 全流程，历史标注 ❌ code=9）、`test_debug_api.py`（逐步调试）、`test_e2e.py`（支持 `--login` 手动登录）。
- **登录相关**：`auto_login_run.py`、`do_login_sync.py`、`manual_login.py`、`pw_login.py`、`confirm_login_test.py`、`test_login.py`。
- **搜索拦截与分析**：`intercept_search.py`、`intercept_after_login.py`、`intercept_live_search.py`、`intercept_real_search.py`。
- **浏览器交互**：`browser_search.py`、`test_search_in_browser.py`、`test_search_via_browser.py`、`test_search.py`。
- **字段/解密验证**：`test_fields.py`、`analyze_search_js.py`、`debug_test.py`、`debug_test2.py`。
- **早期/废弃**：`test_mcp.py`、`test_set_cookies.py`、`final_test.py` 等。

## 5. MCP 能力与接口

MCP Server 名：`中国裁判文书检索`；注册工具 14 个（含 1 个废弃）：

| 工具 | 关键参数 | 返回 | 需登录 |
|------|----------|------|--------|
| `init_session` | 无 | 初始化状态、Cookie 加载数量 | 否 |
| `auto_login_tool` | `username`, `password` | 登录成功/失败、Cookie 数 | — |
| `set_cookies_tool` | `cookies`（字符串或 JSON） | 设置结果、登录状态 | — |
| `search_judgments_tool` | 17 个搜索参数（全文关键词/位置、案件类型、法院层级、案号、法院、当事人、法官、律师、律所、日期范围、法律依据、关键字、案例等级、省份、排序、分页） | 总数 + 文书列表 | 是 |
| `get_judgment_by_case_number_tool` | `case_number` | 匹配文书列表 | 是 |
| `search_party_litigation_tool` | `party_name`, `case_type`, 日期范围, 分页 | 文书列表 + 案件类型分布统计 | 是 |
| `search_judge_cases_tool` | `judge_name`, `court_name`, `case_type`, 分页 | 文书列表 + 类型分布 | 是 |
| `search_legal_basis_cases_tool` | `legal_basis`, `case_type`, 分页 | 引用该法条的文书列表 | 是 |
| `search_reasoning_cases_tool` | `keyword`, `case_type`, `court_level`, 日期范围, 分页 | 本院认为段命中列表 | 是 |
| `search_guided_cases_tool` | `keyword`, `case_type`, 分页 | 指导性案例/优秀文书列表 | 是 |
| `get_dictionaries_tool` | 无 | 全文位置/案件类型/法院层级/文书类型/案例等级/省份/排序字段字典 | 否 |
| `get_website_stats_tool` | 无 | 今日新增、文书总量、访问总量 | 否 |
| `get_status_tool` | 无 | 初始化状态、登录状态、请求统计 | 否 |
| `set_csrf_token_tool` | `token`（任意） | 仅返回“已废弃”说明 | — |

**返回结构**：搜索结果统一为 `{"total": N, "total_displayed": M, "documents": [{...}]}`；错误时返回 `{"error": "...", "total": 0, "documents": []}`。

**注意**：README 工具表写 14 个；DEVELOPER_GUIDE 中把废弃工具也计入。`search_judgments_tool` 实际签名没有“案由”，但有 case_name/court_name 等 17 个字段。

## 6. 登录与反爬方案

### 登录方式

1. **自动登录（推荐）**：`auto_login_tool` / `login_wenshu.py` / `login_with_captcha.py`
   - 访问登录页 → 获取 OAuth URL（`account.court.gov.cn/oauth/authorize`）→ 直接导航 OAuth 页 → 填手机号/密码 → ddddocr OCR 验证码（最多 8 次重试）→ 提交。
   - 登录成功重定向回 wenshu 后：**等待 5 秒 → 重新加载首页 → 再等 3-5 秒**，再提取 Cookie（关键时序，防止拿到 WAF 未完成时的“预防火墙”SESSION）。
   - 提取时过滤 `wzws_` 前缀，持久化到 `wenshu_cookies.json`。
2. **手动 Cookie**：`set_cookies_tool` 或 `cookies_input.json`，支持 `key=value; key2=value2` 或 JSON 字符串。

### 反爬机制与应对

| 防护层 | 本项目应对 |
|--------|-----------|
| `__RequestVerificationToken` | 结论：前端本地 `base.random(24)` 生成，服务端只校验存在；代码每次请求本地生成 24 位随机串 |
| `ciphertext` 反爬令牌 | `cipher.py` 按前端算法生成二进制令牌 |
| DES3 响应加密 | `crypto.py` 3DES/CBC/PKCS7，IV 为当天 `yyyyMMdd`，密钥取响应 `secretKey` |
| 固定 `pageId` | 搜索模块固定 ID `6f08bb13c52123bee1d0c4cc5100c94a`（旧随机 UUID 会导致 code=-13） |
| 排序字段 | 统一 `s50:desc`（旧 `s51` 错误） |
| headless 检测 | `PLAYWRIGHT_HEADLESS=False`，登录必须可见浏览器 |
| WAF Cookie 时序 | 登录后等 5s + reload 首页 + 再等 3-5s 才取 Cookie |
| 频率限制 | `rate_limiter` 1500ms 最小间隔；code=-11 自动指数退避重试 3 次 |
| 错误码 | code=1 成功；-4 未登录；-11 频繁/验证码；-12 无权限；-13 参数错；-14 IP 封禁；code=9 WAF 拒绝 |
| API 通道 | 当前 `api_client.py` 用 httpx 直发（带 Cookie），不再经 Playwright fetch，避免 TLS 指纹绑定 |

### 已知问题

- `docs/CURRENT_STATUS.md` 记录：`WenshuAPIClient.init_session() → search_documents()` 曾稳定返回 code=9；直接 `page.evaluate(fetch)` 可成功。根因指向 Cookie 时序/页面状态污染/API 通道。
- `DEVELOPER_GUIDE.md`（7/23 更新）声称已改为 httpx 直发并修复；`logs/api_capture_log.txt` 7/23 有 code=1 成功记录，但未见 API Client 全流程最终确认记录。
- `wenshu_cookies.json` 中仍含 `wzws_reurl`（网宿 WAF 重定向记录），与“过滤 wzws_ 前缀”的说法不完全一致。
- 登录脚本依赖 `.env`，但 pyproject 未声明 `python-dotenv` 依赖（脚本直接 `from dotenv import load_dotenv`），可能依赖 .venv 环境残留安装。

## 7. 数据资产

| 资产 | 路径 | 说明 |
|------|------|------|
| 登录 Cookie | `wenshu_cookies.json` | 5 个 Cookie，含 `SESSION`（wenshu 域）与账号中心 3 个 Cookie，敏感 |
| Cookie 输入样例 | `cookies_input.json` | 含早期明文 SESSION（敏感，建议清理或加入 .gitignore） |
| 验证码缓存 | `captcha_cache.json` | 4 条图片哈希→OCR 结果 |
| 抓包日志 | `logs/api_capture_log.txt` | 3974 行，记录 7/23 成功搜索请求/响应/解密结果 |
| 调试截图 | `screenshots/`（18 张） | 验证码、登录页、搜索页、重试过程 |
| WAF 与结果截图 | `debug_waf_check.png`, `debug/waf_blocked*.png`, `search-results-4447.png`, `judgment-detail.png` | WAF 拦截与搜索结果证据 |
| code=9 调试目录 | `debug/code9_20260721_*/` | 4 个目录，含 pre/post 截图等调试产物 |
| 页面快照 | `account_center.md`, `wenshu-home.md`, `wenshu-login-page.md`, `wenshu_login.md`, `wenshu_page.md`, `wenshu_filled.md`, `home_state.md`, `current_state.md` | Playwright 可访问性树 dump（调试遗留） |
| 参考资料 | `参考资料/` | 逆向文章 2 篇、类案检索业务规则、MCP 教程、案情描述等 9 份 |
| 测试/脚本 | `tests/`（24 个）、`scripts/`（17 个） | 大量可复用的登录/搜索/抓包/诊断脚本 |

## 8. 当前状态与风险

### 能跑通/已验证

- DES3 加解密、ciphertext 生成、Token 本地生成、搜索字段编码、固定 pageId：均已验证。
- 浏览器手动登录 + 手动搜索：`tools/capture_browser.py` 7/23 成功，搜索“交通事故”返回 655 万条，响应解密成功，字段结构 dict 格式已确认。
- 自动登录（OAuth + OCR + WAF 时序）：`login_with_captcha.py` / `playwright_manager.auto_login` 有完整实现；`quick_test.py`、`e2e_verify_fix.py` 显示登录后可验证 currentUser。
- 网站统计（无需登录）接口已有实现。

### 卡点/不确定性

- `WenshuAPIClient` 全流程是否稳定返回 code=1，未见最终验证报告；7/21 文档与 7/23 文档结论不一致。
- 登录 OCR 依赖 ddddocr 识别率，验证码 8 次重试未必保证成功。
- Cookie 有效期不稳定（几天到几周），SESSION 过期后需人工/自动重登。
- 搜索结果仅摘要（前 200 字），无法看全文；详情页需要另接 docId 页面。

### 风险点

1. **合规风险**：该项目绕过/适配裁判文书网 WAF 与加密反爬，涉及对司法机关公开网站的自动化访问；若用于批量抓取、商业尽调或向公众提供服务，可能违反网站服务条款、Robots 约定及《数据安全法》《个人信息保护法》（裁判文书含大量个人信息）。法律助手 demo 若直接复用，必须限定检索用途、频率并评估授权。
2. **账号与凭据风险**：`.env`、`wenshu_cookies.json`、`cookies_input.json` 均含明文账号密码或 SESSION，且位于项目目录内；若打包/上传可能泄露。
3. **稳定性风险**：WAF 策略、接口参数、响应格式随时可能变更；项目依赖固定 `pageId`、固定密钥、当天 IV 等逆向结论，官方页面改版即可能失效。
4. **IP/账号封禁风险**：触发 code=-11/-14 后需人工处理或换 IP；服务端 IP 部署风险更高。

## 9. 与法律助手 demo 的关联

### 可直接复用

1. **MCP Server 骨架**：`src/wenshu_mcp/server.py` + `tools/definitions.py` + `config.py` + `models/schemas.py`，工具命名与错误处理模式可直接作为法律助手检索后端。
2. **场景化检索逻辑**：案号查询、当事人涉诉、法官画像、法条适用、裁判说理、指导案例 6 类工具正是法律助手的高价值能力，`_format_search_result` 与 `SearchCondition` 可直接复用。
3. **登录脚本**：`playwright_manager.auto_login` / `login_with_captcha.py` / `auto_login_refresh.py` 可复用于 demo 的“登录并维护 Cookie”环节。
4. **加密/令牌生成**：`crypto.py`、`cipher.py`、`generate_token` 是与官网通信的必需组件，可直接抽为独立包。
5. **抓包与诊断工具**：`tools/capture_browser.py` 可用于后续接口变更时重新抓包分析。

### 需要改造

1. **API 通道确认**：先跑通 `WenshuAPIClient.init_session() → search_documents()` 并固定验证用例，确认 httpx 直发稳定；否则退回 Playwright fetch 方案或混合方案。
2. **登录态管理**：当前全局单例 `_client` 不支持多用户；demo 需要按用户隔离 Cookie、加密存储、过期检测与自动重登。
3. **凭据安全**：`.env`、Cookie 文件需移出仓库、加 .gitignore、改加密存储；demo 不应读取 `cookies_input.json` 这类明文样例。
4. **合规限制**：增加检索用途声明、频率控制、结果缓存、审计日志；对当事人个人信息做最小化展示；考虑只读公开摘要而非绕过限制。
5. **错误处理与可观测**：统一 code=9/-11/-14 的用户提示；增加 MCP 层日志与指标。
6. **部署形态**：当前仅 STDIO 本地运行；若 demo 为服务端形态，需评估 SSE/HTTP 传输与 IP 风控。
