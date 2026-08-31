# wenshu_api — 中国裁判文书网爬虫 API 封装

基于 requests 的轻量级封装，把 `https://wenshu.court.gov.cn` 的检索能力包装成
清晰的 Python 类接口。覆盖：关键词组合查询、数据库结构获取、结果列表分页、
文书下载，以及限流 / 重试 / Cookie 管理 / 反爬应对等健壮性处理。

> ⚠️ **用途与合规提示**
> 裁判文书依法属于公开文书，本工具仅用于**合法的研究、法律检索与数据分析**。
> 请遵守站点 `robots` 与服务条款，控制请求频率，勿用于批量抓取、商业倒卖或
> 对站点造成压力的用途。因站点反爬策略持续升级，本库需按“反爬机制 / 校准”
> 一节定期维护。

---

## 1. 安装

```bash
pip install requests          # 唯一硬性依赖
# PDF 下载（可选，三选一；推荐 reportlab，纯 Python 无需原生库）：
pip install reportlab         # 推荐：纯 Python，内置 STSong-Light 中文字体，必定可渲染
pip install weasyprint        # 备选：CSS 还原度最高，但需系统 cairo/pango 原生库
pip install pdfkit            # 备选：需系统安装 wkhtmltopdf 二进制
```
> PDF 后端自动择优：`weasyprint` → `pdfkit` → `reportlab`。任一缺失或运行时失败
> （如 weasyprint 缺 `libgobject`、pdfkit 缺 `wkhtmltopdf`）都会自动回退到下一个，
> 最终落到纯 Python 的 `reportlab`（中文用内置 STSong-Light，无需任何外部字体）。

```python
from wenshu_api import WenshuClient
```

---

## 2. 快速开始

```python
from wenshu_api import WenshuClient

client = WenshuClient(max_qps=1.0)   # 每秒最多 1 个请求，避免被限流
client.login(cookies={"SESSION": "你的已登录SESSION"})  # 先登录（见第 6 节）

# 关键词组合查询（功能 1 + 3）
result = client.search(
    keyword="合同纠纷",
    case_type="民事案件",     # 可传中文或枚举 xs/ms/xz/pc/zx
    court_name="最高人民法院",
    page=1,
    page_size=10,
)
print("命中:", result.total, " 页:", result.total_pages)
for doc in result.documents:
    print(doc.title, "|", doc.case_number, "|", doc.court_name, "|", doc.publish_date)

# 下载文书全文（功能 4）
path = client.download_document(doc.doc_id, save_format="text")
print("已保存:", path)
```

---

## 2.1 命令行工具（CLI）

除 Python API 外，附带开箱即用的命令行工具 `wenshu_api/cli.py`（零额外依赖）。

```bash
# 在项目根目录运行
python wenshu_api/cli.py search "合同纠纷" --case-type 民事案件 --page 1
python wenshu_api/cli.py structure
python wenshu_api/cli.py court-tree --max-depth 2
python wenshu_api/cli.py cause-tree
python wenshu_api/cli.py download --doc-id <ID> --format text --out ./docs

# 也可用模块方式
python -m wenshu_api.cli search "知识产权"
```

**交互式验证码**：遇到验证码时，CLI 自动把图片保存到 `./captcha/`
（可用 `--captcha-dir` 修改）并在终端提示输入；非交互环境则保存图片后报错，
方便你换到交互终端手动完成。加 `--no-captcha` 可关闭交互提示。

全局参数：`--max-qps` / `--timeout` / `--max-retries` / `--proxy` /
`--captcha-dir` / `--no-captcha`。`search` 支持 `--json` 输出便于管道处理。

### 2.2 持久交互终端（REPL，推荐用于手动测试）

比起一次性脚本调用，更推荐进入交互终端：会话常驻、Cookie 复用，且
**验证码只解一次**（解出的 number 在同一会话内缓存复用，直到服务端拒绝才重解）。

```bash
python wenshu_api/cli.py shell          # 或 python -m wenshu_api.cli shell
```

终端内命令（输入 `help` 查看）：

```
wenshu> search 合同纠纷 --case-type 民事案件 --page 1
wenshu> structure
wenshu> court-tree --max-depth 2
wenshu> cause-tree
wenshu> download --doc-id <ID> --format text --out ./docs
wenshu> reset          # 重置会话（清空 Cookie 与验证码缓存，重新初始化）
wenshu> captcha        # 强制重新解一次验证码
wenshu> exit           # 退出
```

首次查询会保存验证码图片到 `./captcha/` 并提示输入；输入后本次会话后续命令
自动复用，无需反复输入。全局参数同样适用于 shell，如
`python wenshu_api/cli.py shell --no-captcha`。

### 2.3 浏览器后端模式（推荐用于真实环境）⭐

`requests` 后端把 OAuth 登录拿到的 `SESSION` Cookie 注入 `requests.Session` 重放，
但裁判文书网的 **wzws 防火墙会把「校验通过」绑定在当初通过挑战的浏览器会话上**，
跨上下文重放的 SESSION 即使 Cookie 完全正确，也会被**软拦截**（网关返回 `code=1`
但 `resultCount=0` 空结果，而非 `code=9`）。因此 `requests` 后端对自动抓取的
SESSION 往往 0 命中（详见第 5 节）。

**浏览器后端模式**把搜索/下载的网关请求放在一个**常驻的 Playwright 浏览器上下文**
里发（`page.evaluate(fetch ...)`），自动携带该上下文的 Cookie 与浏览器指纹，从而
被 WAF 认可、稳定绕过软拦截。响应回到 Python 后照常 3DES 解密。**真实环境建议用此模式。**

```python
from wenshu_api import WenshuClient

# 浏览器后端：首次 search/download 会自启 OAuth 登录（保持浏览器常驻）
client = WenshuClient(backend="browser", max_qps=1.0)
result = client.search("买卖合同", page=1)          # 请求在浏览器内发出，绕过软拦截
doc = client.get_document_content(result.documents[0].doc_id)
client.download_document(doc.doc_id, save_format="pdf", save_path="./docs")
client.close()                                       # 关闭浏览器，释放资源
```

- **OAuth 路径**：`backend="browser"` 且未传 `cookies` 时，首次访问自动驱动真实
  浏览器走 OAuth 登录（含 ddddocr 验证码识别），登录成功后浏览器保持常驻。
- **注入 Cookie 路径**（可靠）：把你**自己当前已登录浏览器**导出的 SESSION 注入，
  浏览器后端会把它写进浏览器上下文并导航首页建立 WAF 信任：
  ```python
  client = WenshuClient(backend="browser")
  client.login(cookies={"SESSION": "你的SESSION"})   # 或 Cookie 串 / JSON 文件
  ```
- **前置依赖**：`pip install playwright && playwright install chromium`。
- **CLI**：`python wenshu_api/cli.py --backend browser search "买卖合同"`；
  REPL 内可用 `backend browser` / `backend requests` 运行时切换后端。

> 浏览器后端与 `requests` 后端的搜索/下载结果数据结构完全一致（均经 3DES 解密 +
> `_parse_document` 解析），差异只在「请求由谁发出」。浏览器后端吞吐低于纯 HTTP，
> 但能稳定命中真实数据，是真实环境的推荐选择。

---

## 3. 五大功能对照

| 功能 | 方法 | 说明 |
|------|------|------|
| 1. 关键词查询 | `search(...)` | 关键词 / 案由 / 法院 / 案件类型 / 审判程序 组合查询 |
| 2. 数据库结构 | `get_db_structure()` / `get_court_tree()` / `get_case_types()` / `get_cause_tree()` | 可查询字段、法院层级、案件类型、案由树 |
| 3. 结果列表 | `search(...)` / `list_documents(query_condition, ...)` | 摘要列表 + 分页 |
| 4. 文书下载 | `download_document(...)` / `get_document_content(...)` | 文本 / PDF，按 docId |
| 5. 异常处理 | 内置 | 限流 / 重试 / Cookie / 反爬 / 超时 |

### 3.1 关键词组合查询

```python
result = client.search(
    keyword="民间借贷",        # 全文检索
    cause="民间借贷纠纷",       # 案由
    court_name="北京市高级人民法院",
    case_type="民事案件",       # 或 "ms"
    trial_procedure="二审",
    page=1, page_size=20,
)
```

### 3.2 数据库结构

```python
struct = client.get_db_structure()
print(struct.queryable_fields)   # 可查询字段及示例
print(struct.case_types)         # 案件类型枚举
print(struct.court_levels)       # 法院层级

tree = client.get_court_tree()   # 法院层级树（远程拉取，失败回退本地）
```

### 3.3 结果列表分页

`search()` 返回 `SearchResult`，自带 `total_pages` 属性，方便翻页：

```python
for p in range(1, min(result.total_pages, 5) + 1):
    page = client.search(keyword="知识产权", page=p)
    ...
```

需要精细控制字段键名时，用底层接口：

```python
from wenshu_api import constants as C
conds = [{"key": C.FIELD_KEYS["keyword"], "value": "专利"}]
result = client.list_documents(conds, page=1)
```

### 3.4 文书下载

下载与搜索同走 `/website/parse/rest.q4w` 网关，cfg=`SearchDataDsoDTO@docInfoSearch`，
需已登录 `SESSION` + `ciphertext` + `docId`（= 搜索结果 `doc_id` / `rowkey`），
响应用 `secretKey` 作 3DES 密钥解密。`get_document_content` 返回结构化的
`DocumentContent`（`title` / `court_name` / `case_number` / `cause` / `keywords` /
`legal_basis` / `full_text` 纯文本 / `html` 完整渲染 HTML 等）。

```python
# 取结构化全文
doc = client.get_document_content(doc_id)
print(doc.title, doc.court_name, doc.case_number)
print(doc.full_text)        # 由 s22~s28 结构化字段拼接的纯文本
print(doc.html)             # 站点完整渲染 HTML（qwContent，含防伪字距）

# 落盘：text=纯文本(.txt) / html=完整HTML(.html) / pdf=转PDF(.pdf)
client.download_document(doc_id, save_format="text", save_path="./docs")
client.download_document(doc_id, save_format="html", save_path="./docs")
client.download_document(doc_id, save_format="pdf",  save_path="./docs")  # 见下方 PDF 后端说明
```

> **PDF 生成**：`pdf` 后端自动择优 `weasyprint` → `pdfkit` → `reportlab`（纯 Python，
> 内置 `STSong-Light` 中文字体，**无需任何外部字体/原生库**，中文必定可渲染）。
> 用结构化 `full_text`（无站点防伪字距）排版，阅读体验最佳。`weasyprint`/`pdfkit`
> 若因缺原生依赖不可用，会自动回退到 `reportlab`，不会静默失败。

### 3.5 异常处理

```python
from wenshu_api import (
    WenshuError, NetworkError, RateLimitError,
    CaptchaRequiredError, ParseError,
)

try:
    result = client.search(keyword="数据")
except RateLimitError as e:
    print("被限流，建议等待", e.retry_after, "秒")
except CaptchaRequiredError as e:
    # e.captcha_image 为验证码图片字节，可弹窗或接入打码平台
    solve_and_retry(e)
except NetworkError as e:
    print("网络异常：", e)
```

---

## 4. 反爬机制与校准（重要）

裁判文书网的反爬手段会**周期性升级**，本库把易变点都做成了“可插拔”设计。

### 4.1 搜索网关令牌：ciphertext（2026-07 已逆向并验证）

> ⚠️ 站点反爬模型已升级。**当前搜索网关（`/website/parse/rest.q4w`）校验的核心
> 令牌是 `ciphertext`，不再依赖历史上的 `vjkl5` / `vl5x` / `guid` / `number`**。
> 旧版 vl5x 兼容层（`register_vl5x_generator`）保留但已对当前站点失效。

`ciphertext` 由站点 `strToBinary.js::cipher()` 生成，算法已完整逆向并在
`utils/crypto.py::generate_ciphertext()` 实现、经真实响应反向解密验证：

```js
function cipher() {
    var timestamp = new Date().getTime().toString();  // 毫秒时间戳
    var salt      = $.WebSite.random(24);               // 24 位随机串
    var iv        = yyyyMMdd;                           // 当天日期
    var enc       = DES3.encrypt(timestamp, salt, iv).toString();  // 3DES/CBC/Pkcs7
    var str       = salt + iv + enc;
    return strTobinary(str);   // 每字符 charCodeAt().toString(2)，空格分隔
}
```

- `$.WebSite.random(24)` 字符集为 `0-9a-zA-Z`（见 `wenshu_random`）；
- `DES3.encrypt` 复用登录密码同一套 3DES/CBC/PKCS7/base64（密钥=24 位 salt，
  IV=当天 `yyyyMMdd`）；
- 服务端从 `ciphertext` 的明文段解出 `salt+iv`，3DES 解密 `enc` 得到 `timestamp`
  并校验时效，**无需 vjkl5**。

客户端已默认把 `generate_ciphertext()` 作为 `ciphertext` 生成器注入网关请求。
**校准方法（如未来站点再次变更）**：用浏览器（或本仓库 `research/` 下的
Playwright 探针）抓取一条真实搜索请求，定位 `strToBinary.js` 中的 `cipher()`，
把新算法移植到 `generate_ciphertext()` 即可。

### 4.2 验证码（ddddocr 离线识别 + 人工兜底）
本库默认用 **ddddocr**（https://github.com/sml2h3/ddddocr）做离线识别，无需联网打码：

```bash
pip install ddddocr
```

- **验证码接口已确认为 `/code/image`**（GET，带随机参数 bust 缓存），返回
  `image/jpeg`（约 120×45）。历史上的 `/ValiCode/GetCode` 现已废弃（直返 HTML 错误页）。
  客户端 `_get_code()` 直接 GET `/code/image?{random}` 取图，交给求解器识别。
- 默认求解器：`DdddOcrSolver`，自动识别并返回文本；识别为空/解码失败会触发
  **刷新重试**（见 4.6）。
- 人工兜底：若 ddddocr 未安装或加 `--no-ocr`，回退交互式输入（需 TTY）。
- 所有验证码图片都会保存到 `--captcha-dir`（默认 `./captcha`），便于审计 OCR
  准确率与排查问题。
- **内嵌 data URI 兜底**：若某页面把验证码以 `data:image/jpg;base64,...` 内嵌，
  提供 `captcha_source_url` 指向该页，客户端会自动正则提取并识别：

```python
from wenshu_api.utils.captcha import build_solver
client = WenshuClient(
    captcha_solver=build_solver(use_ddddocr=True, save_dir="./captcha"),
    captcha_source_url="https://wenshu.court.gov.cn/website/wenshu/181010CARHS5BS3C/index.html?open=login",
)
```

- 实测（2026-07）：`/code/image` 返回真图，`ddddocr` 稳定识别（如 `473P`/`CV2K`/`HoFA`），
  离线验证码管道已打通。后续搜索/下载请求里的 `number` 参数即取自此识别结果。
- 仍可自定义求解器（如接第三方打码平台）后注入 `captcha_solver`。

### 4.3 频率限制与封锁
- 内置令牌桶限流（默认 1 QPS），可调 `max_qps` / `min_interval`；
- 检测到 `429/503` 或业务层“操作过于频繁 / 请先验证”会抛出对应异常；
- HTTP 层瞬时错误按指数退避自动重试（默认 3 次，`max_retries` 可调）。

### 4.4 请求头 / Cookie / 代理
- 随机 UA 池降低指纹集中度；
- 会话用 `requests.Session` 统一管理 Cookie（含 vjkl5）；
- 支持 `proxy`（如企业出口 IP）规避单 IP 封锁。

### 4.5 搜索请求的其它必带参数（2026-07 实测）

`ciphertext` 只是其一。一次能通过校验的搜索请求（POST `/website/parse/rest.q4w`）
还需：

| 参数 | 来源 | 说明 |
|------|------|------|
| `cfg` | 固定 DTO 类 | 搜索主接口 `com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@queryDoc`；另有 `@wsCountSearch`(计数) / `@leftDataItem`(分组) / `@smartTipSearch`(联想) |
| `ciphertext` | `generate_ciphertext()` | 反爬令牌（见 4.1） |
| `__RequestVerificationToken` | 前端 `base.random(24)` 本地生成 | 站点框架 `$.WebSite` 在 body 注入随机隐藏域并直接读取其值（**非服务端下发**）；本库用 `wenshu_random(24)` 本地生成，无需抓取页面 |
| `pageId` | 搜索页模块 id（固定值） | `6f08bb13c52123bee1d0c4cc5100c94a`（见 `constants.PAGE_ID`，若线上变化重跑 `research/oauth_login.py` 抓最新） |
| `s21` / `queryCondition` / `sortFields` / `groupFields` | 查询条件 | `s21`=关键词；`queryCondition`=JSON 化条件 |
| `wh` / `ww` / `cs` | 窗口尺寸 | `wh=720&ww=1280&cs=0`（形式参数） |
| 会话 Cookie | OAuth 登录后下发 | **必须已登录**（见 6. 登录 / OAuth） |

**下载接口（docInfoSearch，2026-07 已校准并端到端验证）** 与搜索同网关
`/website/parse/rest.q4w`，必带参数：

| 参数 | 来源 | 说明 |
|------|------|------|
| `cfg` | 固定 DTO 类 | `com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@docInfoSearch` |
| `docId` | 搜索结果 `doc_id` / `rowkey` | 文书唯一标识 |
| `ciphertext` | `generate_ciphertext()` | 反爬令牌（同搜索） |
| `__RequestVerificationToken` | `wenshu_random(24)` | 同搜索，本地生成随机串 |
| `wh` / `ww` / `cs` | `720` / `1280` / `0` | 窗口尺寸形式参数 |
| Referer | 详情页 URL | `…/website/wenshu/181107ANFZ0BXSK4/index.html?docId=<docId>` |
| 会话 Cookie | OAuth 登录后下发 | **必须已登录** |

> ⚠️ **User-Agent 敏感**：裁判文书网对 UA 校验严格，`docInfoSearch` / `queryDoc`
> 网关均**拒绝非 Chrome UA**（含 Firefox、macOS/Linux 上的 Chrome）。本库已默认把
> `DEFAULT_USER_AGENT` 固定为 Windows Chrome；若用 `WenshuClient(headers=...)` 覆盖
> UA，请务必传 Chrome UA，否则会收到 `code=9 没有权限请求接口`（易误判为登录失效）。

> `search()` 与 `download_document()` **均已实现并端到端验证**：自动持有 `SESSION`、
> 生成 `ciphertext`、本地生成 `__RequestVerificationToken`，向 `/website/parse/rest.q4w`
> 发 `queryDoc` / `docInfoSearch`，并解密响应（见 4.7）。只需先 `login()` 拿到已登录会话即可使用。

### 4.6 可观测日志（成功/失败/重试/刷新）
内置 `logging`，统一 logger 名 `wenshu_api`，关键状态均带方括号标签：

| 标签 | 含义 |
|------|------|
| `[会话]`   | 会话初始化（vjkl5） |
| `[验证码]` | 获取图片 / ddddocr 识别成功 / 识别为空 / 接口返回 HTML |
| `[限流]`   | HTTP 429/503，建议等待秒数 |
| `[重试]`   | 网络错误指数退避重试（第 N 次） |
| `[验证码] 刷新并重试` | 识别失败或服务端拒绝，重新取图再识（第 N/max 次） |
| `[成功]`   | 命中 N 条 / 文书已保存 |

用法：

```python
from wenshu_api.utils.log import configure_logging
configure_logging(level=logging.INFO)   # DEBUG 看更细的请求/重试
```

CLI 用 `--debug` 开 DEBUG；验证码刷新重试次数用 `--max-captcha-retries`（默认 5）。
验证码“识别失败/服务端拒绝”会清空缓存、重新获取图片并再次识别，直到成功或达到
最大重试次数。

### 4.7 响应解密（DES3.decrypt + secretKey）

`queryDoc` 等接口的响应体是 **3DES 加密**的（逆向自 lawyee 框架 `DES3.decrypt`）：

```js
// 框架 $.ajax success 回调
if (data.secretKey) {
    var obj = DES3.decrypt(data.result, data.secretKey);  // 3DES/CBC/Pkcs7, iv=当天 yyyyMMdd
    data.result = $.parseJSON(obj);
}
```

即：用响应里的 `secretKey` 作 3DES 密钥、`iv=当天 yyyyMMdd`，对 `result`（base64）
做 3DES/CBC/PKCS7 解密，得到 JSON 明文（含 `Count` 与 `data` 列表）。

本库在 `utils/crypto.des3_decrypt()` 复刻该逻辑，`WenshuClient._decrypt_payload()`
会在搜索后自动解密。若线上把 `iv` 改用其它值（如请求 salt），仅需调整
`des3_decrypt` 的 `iv` 参数。

---

## 5. 已知限制

- 站点接口与反爬策略频繁变动，本库需随线上版本维护（重点：`ciphertext` 算法、
  `pageId` / 搜索应用路径、响应加解密、字段键名、docInfoSearch 的 `docId` 形态）。
- **下载接口已校准（2026-07）**：`download_document` / `get_document_content` 现走
  `docInfoSearch` 新协议（`/website/parse/rest.q4w` + `ciphertext` + 已登录 `SESSION`），
  已端到端验证可拉取真实文书全文（结构化字段 + 完整 HTML）。旧版 `DOC_CONTENT_URL` 已弃用。
- **验证码识别准确率依赖样式**：ddddocr 对常规字符验证码有效，但对点选/滑块/
  扭曲严重的验证码可能失准；识别失败会自动刷新重试，仍失败则需人工/第三方打码。
- **验证码接口已确认（2026-07）**：真实验证码接口是 `/code/image`（GET + 随机参数，
  返回 image/jpeg），`ddddocr` 离线识别稳定。旧的 `ValiCode/GetCode` 已废弃（直返 HTML
  错误页），本库已切到 `/code/image`。若日后该接口也变动，需在浏览器核对 `code/image`
  的真实请求方式后校准 `constants.py`（GET_CODE_URL / LOGIN_PAGE_URL）。
- **搜索网关已打通（2026-07）**：`search()` 经 OAuth 登录 + `ciphertext` + 本地生成
  `__RequestVerificationToken`/`pageId`，命中 `/website/parse/rest.q4w`，并自动解密
  响应。**不再依赖**历史上的 `vjkl5` / `vl5x` / `guid` / `number`（这些旧字段当前站点
  已不再校验，旧 `register_vl5x_generator` 兼容层保留但已失效）。
- 字段 `s1..sN` 的语义随版本可能漂移，解析结果以 `DocumentMeta.raw` 为准兜底。
- **⚠️ wzws 防火墙会软拦截「跨上下文重放的 SESSION」（2026-07 实测关键坑）**：
  网关对 `code=9`（无权限）之外，还会对「从另一进程/另一上下文重放的已登录
  SESSION」返回 `code=1` 但 **resultCount=0 的空结果**（即登录态被接受、但
  搜索/下载被静默阻断）。已实测：
  - 在**真实浏览器会话内**用 `fetch` 打同一网关 → 命中 6877716 条（正常）；
  - 把该 SESSION 导出、用 `requests` 重放（即使**同一进程、登录后仅数秒**、且带
    上 `wzws_reurl`）→ 仍返回 0 命中。
  说明防火墙把「校验通过」绑定在原浏览器会话上。**结论**：`--cookies` / `login(cookies=)`
  仅在注入的 SESSION **来自当前受防火墙信任的浏览器会话**（如你手动从自己已登录的
  浏览器拷出的 SESSION）时可靠；本库自动 Playwright 抓取的 SESSION 重放可能被软拦截。
  **可靠的端到端路径是把网关请求直接放在已登录的浏览器上下文内执行**——本库现已内置
  **浏览器后端模式**（`WenshuClient(backend="browser")` 或 `cli.py --backend browser`，
  见第 2.3 节），login 后保持 Playwright 上下文，search/download 经 `page.evaluate(
  fetch …)` 发请求并自动解密，无需手工旁路脚本。

---

## 6. 登录模块（账号密码 + 验证码 + 3DES）

本库已实现完整登录流程，凭据通过 `python-dotenv` **脱密加载**，绝不硬编码。

> ⚠️ **2026-07 重要更正**：裁判文书网登录**已改为 OAuth 流程**
> （统一账号中心 `account.court.gov.cn`，client_id=`zgcpwsw`）。本库此前实现的
> `crud/rest.q4w` + `AppUserDTO@login` 直登通道**已被站点废弃**——即使账号密码
> 完全正确，该通道也会返回“登录名或密码错误”（这是废弃通道的统一回包，并非凭据
> 错误）。已用 Playwright 实测：相同 `.env` 凭据走 OAuth 流程可**成功登录**并拿到
> 搜索接口所需的 `SESSION` Cookie。完整 OAuth 登录+抓包脚本见 `research/oauth_login.py`。
> 后续若要 `search()` 真正返回结果，**须先经 OAuth 拿到已登录会话**（或手工从浏览器
> 导出 `SESSION` Cookie 注入客户端）。

### 6.1 配置 .env（脱密）
在 `wenshu_api/.env`（或项目根 `.env`）写入：

```ini
WENSHU_USER_NAME=你的账号
WENSHU_PASSWORD=你的密码
```

> 密码若含 `#`、空格或特殊字符，请用双引号包裹：`WENSHU_PASSWORD="xxx#yy"`，
> 否则 dotenv 会当作注释或截断。

### 6.2 调用

`login()` 支持两条路径：

**路径 A — 注入已登录会话（推荐 / 轻量 / CI）**：从浏览器 DevTools → Application →
Cookies 复制 `wenshu.court.gov.cn` 的 `SESSION` 等，直接注入，无需浏览器依赖：

```python
from wenshu_api import WenshuClient
c = WenshuClient()
c.login(cookies={          # dict 或 Playwright 的 Cookie 列表
    "SESSION": "你的SESSION值",
})
print("logged_in:", c.is_logged_in())
```

> ⚠️ 注入的 SESSION 必须来自**当前受 wzws 防火墙信任的浏览器会话**（见第 5 节）；
> 本库自动 Playwright 抓取的 SESSION 跨上下文重放可能被防火墙软拦截（返回 0 命中）。
> CLI 同理支持全局 `--cookies`：`python wenshu_api/cli.py search 买卖合同 --cookies ./fresh_cookies.json`
> （取值可为 ①JSON 文件路径 ②裸 SESSION 值 ③`SESSION=xxx;other=y` Cookie 串）。

**路径 B — Playwright 自动 OAuth（默认）**：未传 `cookies` 且本机装有 `playwright` +
`ddddocr` 时，驱动真实浏览器完成 OAuth 登录（含 ddddocr 离线识别验证码）：

```python
from wenshu_api import WenshuClient
c = WenshuClient()                 # 构造时自动从 .env 加载凭据
res = c.login()                    # OAuth 登录（凭据读 .env）
print(res["method"], c.is_logged_in())
```

显式传参亦可：`c.login(username="...", password="...")`。登录成功后 `c.logged_in`
置 True，登录态 Cookie 由 `requests.Session` 维持，后续 `search`/`download` 自动复用。

### 6.3 关于密码加密

OAuth 路径由真实浏览器完成账号密码提交，密码加密由站点前端自行处理，本库无需
复刻。库内 `utils/crypto.des3_encrypt()`（3DES/CBC/PKCS7/base64，IV=当天 `yyyyMMdd`）
是早期直登通道的逆向产物，已与站点 CryptoJS **字节级验证一致**，目前作为兼容保留。

### 6.4 CLI / REPL
```bash
python wenshu_api/cli.py login                 # 凭据来自 .env
python wenshu_api/cli.py login --username u --password p
python -m wenshu_api.cli shell                 # REPL 内输入 login
```

### 6.5 日志与重试
`[登录]` 标签记录注入 / OAuth 开始 / 成功 / 失败；OAuth 路径下验证码识别失败会
自动刷新重试（上限由 `max_attempts` 控制，默认 4；CLI 用 `--max-captcha-retries`）。

> 实测（2026-07）：
> - 登录**密码**的 3DES 加密（`des3_encrypt`）已与站点 CryptoJS **字节级验证一致**；
> - 但旧 `crud/rest.q4w` + `AppUserDTO@login` 直登通道已被站点废弃，返回“登录名或密码
>   错误”是废弃通道统一回包，**不代表 `.env` 凭据错误**；相同凭据走 OAuth 已实测
>   **登录成功**并拿到搜索接口所需的 `SESSION` Cookie。当前 `login()` 默认走 OAuth
>   （或注入浏览器导出的 `SESSION` Cookie），不再使用废弃通道。

### 6.6 下载接口（docInfoSearch，已完成校准）

`download_document` / `get_document_content` 现已走 `docInfoSearch` 新协议：
网关 `/website/parse/rest.q4w`，`cfg=SearchDataDsoDTO@docInfoSearch`，
必带 `docId`（= 搜索结果 `doc_id` / `rowkey`）+ `ciphertext` + 已登录 `SESSION`
+ `__RequestVerificationToken` + `wh/ww/cs`，Referer 指向详情页。响应结构与搜索一致
（3DES + `secretKey` 解密），解密体为文书结构化字段（`s1` 案件名称、`s2` 法院、`s7` 案号、
`s8` 类型、`s9` 审判程序、`s22~s28` 正文段落、`s11` 案由、`s45` 关键词、`s47` 法律依据、
`qwContent` 完整渲染 HTML 等）。

> 实测（2026-07）：`get_document_content` 解密出完整文书（`full_text` 5164 字 /
> `html` 9915 字），`download_document(..., save_format="text"|"html"|"pdf")` 均可落盘；
> PDF 经 `weasyprint`(缺原生库)→`pdfkit`(缺二进制)→**`reportlab` 兜底**生成 13KB 中文 PDF。
> **注意**：因 wzws 防火墙软拦截跨上下文重放的 SESSION（见第 5 节），纯 `requests` 重放
> 注入的 SESSION 可能拿到 0 命中；**可靠验证**是在已登录浏览器会话内 `fetch` 网关并把解密
> 数据喂给库代码（`research/e2e_browser_download.py`：queryDoc→rowkey→docInfoSearch→
> `_parse_document`→`_save_as_pdf`，端到端通过）。抓取/复刻脚本见 `research/download_probe.py`
> （Playwright 捕获完整请求）与 `research/replay_docinfo.py`（纯 requests 重放验证）。

---

## 7. 项目设计与架构

本节从整体上说明项目**为什么这样设计**，帮助你快速建立心智模型、定位代码、
以及在站点升级时知道"改哪里"。

### 7.1 设计理念

1. **把"易变的反爬"与"稳定的业务"分离**。站点的令牌算法、加解密、验证码接口、
   登录方式都会周期性变动，因此它们被收敛到 `utils/crypto.py`、`utils/captcha.py`、
   `auth_oauth.py` 等"可插拔"模块；`client.py` 只负责编排业务流程，尽量不含硬编码的
   易变细节。站点升级时，绝大多数改动集中在少数几个点（见 7.5）。
2. **单一门面（Facade）**。对外只暴露一个 `WenshuClient`，五大功能（搜索/结构/列表/
   下载/异常）都是它的方法。使用者不需要理解内部的加解密与反爬细节即可上手。
3. **同一套业务逻辑、可切换的请求后端**。搜索/下载的"拼参数→发请求→解密→解析"链路
   只写一遍，底层"由谁发请求"抽象成 `_gateway_text()` 一个入口，可在 `requests` 与
   `browser` 两种后端间切换（见 7.3），从而兼顾轻量与"绕过 WAF 软拦截"。
4. **健壮性内建、可观测优先**。限流、指数退避重试、验证码刷新重试、结构化日志都是
   默认行为；每个关键状态都有 `[标签]` 日志（见 4.6），便于线上排查。
5. **凭据脱密、绝不硬编码**。账号密码只经 `.env`（`python-dotenv`）加载，代码与日志
   中不出现明文。

### 7.2 分层架构

```
        ┌─────────────────────────────────────────────────────────┐
  入口层 │   cli.py   │   shell.py (REPL)   │   直接 import 的用户代码   │
        └───────────────────────────┬─────────────────────────────┘
                                     │  统一构造 & 调用
        ┌────────────────────────────▼────────────────────────────┐
  门面层 │                       WenshuClient (client.py)            │
        │  search / list_documents / get_*_tree / get_db_structure  │
        │  get_document_content / download_document / login / close │
        │  —— 编排：拼参数 → _gateway_text() → 解密 → 解析为模型 ——  │
        └───┬───────────────┬───────────────┬──────────────────┬───┘
            │               │               │                  │
   ┌────────▼───┐   ┌───────▼────────┐  ┌───▼──────────┐  ┌────▼────────┐
   │ 请求后端    │   │ 反爬 / 加解密   │  │ 登录          │  │ 健壮性 / 观测 │
   │ (可切换)    │   │ utils/crypto   │  │ auth_oauth    │  │ rate_limiter │
   │            │   │  ·ciphertext   │  │  ·OAuth 流程  │  │ retry / log  │
   │ requests ──┼─► │  ·3DES 加/解密 │  │ utils/captcha │  │              │
   │ browser  ──┼─► │                │  │  ·ddddocr     │  │              │
   │(backend_   │   └────────────────┘  └──────────────┘  └─────────────┘
   │ browser.py)│
   └─────┬──────┘
         │  HTTP / 浏览器内 fetch
   ┌─────▼──────────────────────────────────────────────────────────┐
   │            wenshu.court.gov.cn  /website/parse/rest.q4w          │
   │        (queryDoc / docInfoSearch，响应为 3DES 加密信封)           │
   └─────────────────────────────────────────────────────────────────┘

  贯穿各层：constants.py（地址/字段/枚举）· models.py（dataclass 数据模型）
            · exceptions.py（统一异常体系）
```

### 7.3 双后端设计：`requests` vs `browser`

同一套业务链路（拼参数/解密/解析）复用，只有"请求由谁发出"不同。二者通过
`client._gateway_text(url, form, referer)` 统一分派，上层无感知。

| 维度 | `requests` 后端（默认） | `browser` 后端（`backend="browser"`）|
|------|------------------------|--------------------------------------|
| 发请求方式 | `requests.Session` 直接 HTTP 重放 | 常驻 Playwright 上下文内 `page.evaluate(fetch…)` |
| 能否绕过 wzws 软拦截 | ❌ 跨上下文重放常被软拦截（0 命中） | ✅ 请求发自受信任浏览器会话，稳定命中 |
| 依赖 | 仅 `requests` | 额外需 `playwright` + `chromium` |
| 吞吐 | 高 | 较低（浏览器开销） |
| 适用场景 | 单元测试 / 已确信 SESSION 受信任 / CI 打桩 | **真实环境采集（推荐）** |

> 关键取舍：站点的 **wzws 防火墙把"校验通过"绑定在原浏览器会话上**（见第 5 节），
> 这使得纯 HTTP 重放在真实环境不可靠。浏览器后端用"把请求放回受信任上下文"这一招
> 稳定绕过，代价是牺牲吞吐、引入浏览器依赖——因此设计成**可选后端**而非默认。

### 7.4 一次搜索请求的数据流

```
client.search(keyword=…)
   │
   ├─ _ensure_login()                 # 未登录则报错(requests) / 自动 OAuth(browser)
   ├─ 组装 form：cfg=queryDoc + s21 + queryCondition + pageId
   │            + ciphertext (crypto.generate_ciphertext, 本地 3DES)
   │            + __RequestVerificationToken (wenshu_random(24), 本地生成)
   │            + wh/ww/cs
   ├─ limiter.acquire()               # 令牌桶限流 (rate_limiter)
   ├─ _gateway_text(GATEWAY_URL, form, referer)
   │     ├─ requests 后端 → _raw_post()（含 429/503 处理 + 重试）
   │     └─ browser  后端 → BrowserBackend.fetch_gateway()（浏览器内 fetch）
   ├─ _decrypt_payload(text)          # 3DES 解密：secretKey 作密钥, iv=当天 yyyyMMdd
   └─ _parse_list(json) → SearchResult(documents=[DocumentMeta…], total, pages…)
```

下载链路（`get_document_content` / `download_document`）同构，只是 `cfg` 换成
`docInfoSearch`、参数换成 `docId`、解析走 `_parse_document → DocumentContent`，
PDF 落盘再经 `_save_as_pdf` 三级择优（weasyprint→pdfkit→reportlab）。

### 7.5 可插拔的反爬对抗点（站点升级时改这里）

| 易变点 | 现状 | 校准位置 | 抓取/复刻脚本 |
|--------|------|----------|---------------|
| 搜索令牌算法 | `ciphertext`（salt+yyyyMMdd+3DES(timestamp)） | `utils/crypto.generate_ciphertext()` | `research/oauth_login.py` |
| 响应解密 | 3DES，`secretKey` 作密钥、iv=当天日期 | `utils/crypto.des3_decrypt()` | `research/replay_docinfo.py` |
| 登录方式 | OAuth（`account.court.gov.cn`） | `auth_oauth.py` | `research/login_save_cookie.py` |
| 验证码接口 | `/code/image`（GET），ddddocr 离线识别 | `utils/captcha.py` + `constants.py` | `captcha_samples/` |
| 网关字段/枚举 | `pageId` / `cfg` / `s21…` / UA | `constants.py` | `research/download_probe.py` |
| WAF 软拦截 | 会话绑定，需浏览器上下文发请求 | `backend_browser.py` | `research/e2e_browser_download.py` |

> 排障口诀：拿到 `code=9` 多半是 **UA 不是 Chrome**；`code=1` 但 `resultCount=0`
> 多半是 **WAF 软拦截**（换 `browser` 后端）；返回"登录名或密码错误"可能是**废弃直登
> 通道**的统一回包而非凭据错（走 OAuth 复验）。

### 7.6 目录结构

```
wenshu_api/
├── __init__.py         # 公开 API 导出（门面）
├── client.py           # WenshuClient 主类：五大功能编排 + 双后端分派 + 反爬 + 重试
├── auth_oauth.py       # OAuth 登录（Playwright 驱动，keep_open 可保持浏览器常驻）
├── backend_browser.py  # 浏览器后端：常驻 Playwright 上下文，page.evaluate(fetch) 发网关请求
├── cli.py              # 命令行入口（search/structure/.../shell；全局参数 --backend/--cookies…）
├── shell.py            # 持久交互终端（REPL，会话/验证码复用）
├── constants.py        # 接口地址 / 请求头 / 字段映射 / 枚举 / 默认 UA
├── exceptions.py       # 异常体系（NetworkError/RateLimitError/CaptchaRequiredError…）
├── models.py           # 数据模型（SearchResult / DocumentMeta / DocumentContent / LegalBasis…）
├── example.py          # 使用示例脚本
└── utils/
    ├── __init__.py
    ├── rate_limiter.py # 令牌桶限流
    ├── retry.py        # 指数退避重试装饰器（带日志）
    ├── crypto.py       # ciphertext 生成 / 3DES 加密与响应解密 / UA 随机化
    ├── captcha.py      # ddddocr 离线识别 + 交互兜底求解器
    └── log.py          # 统一日志（可观测）
```

对应关系一览：**入口** = `cli.py`/`shell.py`；**门面** = `client.py`；
**请求后端** = `requests.Session`（client 内） / `backend_browser.py`；
**反爬** = `utils/crypto.py`/`utils/captcha.py`；**登录** = `auth_oauth.py`；
**健壮性/观测** = `utils/rate_limiter.py`/`utils/retry.py`/`utils/log.py`；
**共用** = `constants.py`/`models.py`/`exceptions.py`。
