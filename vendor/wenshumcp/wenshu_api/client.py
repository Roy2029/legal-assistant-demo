"""WenshuClient：中国裁判文书网爬虫 API 封装主类。

提供五大功能：
  1. 关键词组合查询      search(...)
  2. 数据库结构获取      get_db_structure() / get_court_tree() / get_case_types()
  3. 结果列表分页获取    list_documents(...) （search 的内部实现，亦可单独调用）
  4. 文书文件下载        download_document(...) / get_document_content(...)
  5. 异常处理与健壮性    限流 / 重试 / Cookie 管理 / 反爬应对 / 超时

典型用法见 example.py 与 README.md。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Callable, Iterable, Optional

import dotenv
import requests

from . import constants as C
from .exceptions import (
    CaptchaRequiredError,
    CaptchaUnavailableError,
    WenshuError,
    DocumentNotFoundError,
    NetworkError,
    ParseError,
    RateLimitError,
    SessionExpiredError,
)
from .models import (
    CourtNode,
    DatabaseStructure,
    DocumentContent,
    DocumentMeta,
    FieldMeta,
    LegalBasis,
    SearchResult,
)
from .utils.captcha import extract_data_uri
from .utils.crypto import (
    des3_encrypt,
    des3_decrypt,
    get_vl5x,
    random_guid,
    generate_ciphertext,
    wenshu_random,
)
from .utils.log import configure_logging, get_logger
from .utils.rate_limiter import RateLimiter
from .utils.retry import retry

# 列表响应字段（编号键，与站点 s1..sN 对外字段对应）-> DocumentMeta 属性。
# 主解析逻辑已直接映射数值键（"1"/"2"/"7"/"31"/"26"/"rowkey"），此表用于
# 站点万一切回旧 s1..sN 命名时的兼容兜底。
_LIST_FIELD_MAP = {
    "rowkey": "rowkey",
    "docId": "doc_id",
    "s1": "title",                # 案件名称
    "s2": "court_name",           # 法院名称
    "s3": "case_type",            # 案件类型
    "s4": "cause",                # 案由
    "s7": "case_number",          # 案号
    "s8": "title",                # 部分版本标题在此
    "s31": "publish_date",        # 日期
    "s26": "summary",             # 本院认为/裁判要旨
    "s10": "trial_procedure",     # 审判程序
    "s50": "publish_date",        # 发布日期
}

# 命中总数在响应中可能出现的键名
_TOTAL_KEYS = ("Count", "count", "total", "totalCount", "TotalCount")

# 站点在标题/摘要里会把命中的关键词包成 <span style="color:red">…</span> 高亮，
# 解析时剥掉标签只留纯文本。
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """剥离站点注入的高亮/样式标签，返回纯文本。"""
    if not s:
        return s
    return _TAG_RE.sub("", s)


class WenshuClient:
    """中国裁判文书网爬虫客户端。

    参数：
        max_qps:            限流上限（每秒请求数）。默认 1.0，建议保守。
        min_interval:       等价的最小请求间隔（秒）。与 max_qps 二选一。
        timeout:            单次请求超时（秒）。默认 15。
        max_retries:        网络瞬时错误重试次数。默认 3。
        headers:            额外/覆盖的请求头。
        captcha_solver:     验证码求解回调，签名 (image_bytes, image_url) -> str。
                            为 None 时遇到验证码将抛出 CaptchaRequiredError。
        proxy:              可选代理地址，如 "http://127.0.0.1:7890"。
        verify:             SSL 校验开关，默认 True。
        ciphertext_generator: 若线上版本要求 AES 加密 param，注入
                             (param_dict) -> ciphertext 的函数（见 README）。
        max_captcha_retries:  验证码“识别失败/服务端拒绝”时的最大刷新重试次数。
                             默认 5。每次刷新会重新获取图片并再次识别。
        captcha_source_url:   当验证码以内嵌 data URI 形式存在于某个页面/JS 时，
                             提供该页面 URL，客户端会抓取并提取验证码图片。
                             默认 None（先尝试旧版 GetCode 接口）。
        log_level:            日志级别（logging 模块常量）。默认 INFO，可设 DEBUG
                             观察更细的请求/重试过程。
    """

    def __init__(
        self,
        max_qps: Optional[float] = 1.0,
        min_interval: Optional[float] = None,
        timeout: int = C.DEFAULT_TIMEOUT,
        max_retries: int = 3,
        headers: Optional[dict] = None,
        captcha_solver: Optional[Callable[[bytes, str], str]] = None,
        proxy: Optional[str] = None,
        verify: bool = True,
        ciphertext_generator: Optional[Callable[[dict], str]] = None,
        max_captcha_retries: int = 5,
        captcha_source_url: Optional[str] = None,
        log_level: int = 20,  # logging.INFO
        backend: str = "requests",
    ):
        """后端模式（backend）：
            - "requests"（默认）：纯 HTTP，登录后把 SESSION 注入 requests.Session
              重放。注意：wzws 防火墙会软拦截跨上下文重放的 SESSION（code=1 但空结果），
              因此该模式对自动抓取的 SESSION 可能 0 命中（详见 README 第 5 节）。
            - "browser"：保持一个 Playwright 浏览器上下文，search/download 在浏览器
              内发请求，绕过软拦截，真实环境稳定可用。需本机安装 playwright + chromium。
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.captcha_solver = captcha_solver
        # 2026-07 实测：当前站点搜索网关校验的核心令牌是 ciphertext
        # （generate_ciphertext），不再依赖 vl5x。若调用方未注入自定义生成器，
        # 默认使用从线上逆向并验证过的实现。
        self.ciphertext_generator = ciphertext_generator or (lambda _param: generate_ciphertext())
        self.verify = verify
        self.max_captcha_retries = max_captcha_retries
        self.captcha_source_url = captcha_source_url
        self.logger = configure_logging(level=log_level)

        self.limiter = RateLimiter(max_qps=max_qps, min_interval=min_interval)

        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        # 基础请求头 + 固定 Windows Chrome UA（站点拒绝非 Chrome UA）
        self._headers = dict(C.DEFAULT_HEADERS)
        self._headers["User-Agent"] = C.DEFAULT_USER_AGENT
        if headers:
            self._headers.update(headers)

        self.vjkl5: str = ""
        self._initialized = False
        # 登录态标记（login() 成功后置 True）
        self.logged_in: bool = False
        # 后端模式：requests（默认）或 browser（常驻 Playwright 上下文，绕过 wzws 软拦截）
        if backend not in ("requests", "browser"):
            raise ValueError(f"backend 仅支持 'requests' / 'browser'，收到：{backend!r}")
        self._backend_mode = backend
        self._browser_backend = None
        # 已解出的验证码 number 缓存：同一持久会话内复用，避免每次人工输入。
        # 服务端拒绝（触发验证码）时由 _handle_response 清空，下次重新解。
        self._captcha_number: str | None = None
        # 加载 .env 脱密凭据（不覆盖已存在的环境变量）
        self._load_env()

    # ------------------------------------------------------------------ #
    # 会话与反爬基础设施
    # ------------------------------------------------------------------ #
    def init_session(self) -> None:
        """建立会话：访问首页拿到初始 Cookie（含 vjkl5 雏形）。"""
        self._captcha_number = None
        self.limiter.acquire()
        try:
            resp = self.session.get(
                C.HOME_URL,
                headers=self._headers,
                timeout=self.timeout,
                verify=self.verify,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"访问首页失败：{exc}") from exc

        self.vjkl5 = self.session.cookies.get("vjkl5", "")
        self.logger.info("[会话] 已初始化（vjkl5=%s…）", (self.vjkl5 or "?")[:10])
        if not self.vjkl5:
            # 部分版本首页不直接下发 vjkl5，需要借助验证码接口“预热”
            self._warm_up_vjkl5()
        self._initialized = True

    def _warm_up_vjkl5(self) -> None:
        """通过验证码接口预热，间接获得 vjkl5 Cookie。"""
        try:
            self.session.get(
                C.GET_CODE_URL,
                headers={**self._headers, "Referer": C.HOME_URL},
                timeout=self.timeout,
                verify=self.verify,
            )
        except requests.RequestException:
            pass  # 预热失败不致命，后续请求可能仍带 vjkl5
        self.vjkl5 = self.session.cookies.get("vjkl5", self.vjkl5)

    def _ensure_session(self) -> None:
        if not self._initialized:
            self.init_session()

    def _get_code(self) -> str:
        """获取验证码 number（图形码 OCR 出的文本）。

        返回形态：
        1. 内嵌 data URI（captcha_source_url 指向的页面含 data:image）；
        2. 新版 /code/image 接口返回 image/jpeg（默认路径，ddddocr 离线识别）；
        3. 旧版 GetCode 返回明码 JSON（极少用）。
        """
        # 优先尝试内嵌 data URI（captcha_source_url 指向的页面含 data:image）
        image_bytes = self._fetch_embedded_captcha()
        if image_bytes:
            return self._solve_captcha_image(image_bytes, "embedded")

        # 主路径：/code/image?{random} 返回 image/jpeg
        url = f"{C.GET_CODE_URL}?{random.random()}"
        self.limiter.acquire()
        try:
            resp = self.session.get(
                url,
                headers={**self._headers, "Referer": C.LOGIN_PAGE_URL},
                timeout=self.timeout,
                verify=self.verify,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"获取验证码失败：{exc}") from exc

        ctype = resp.headers.get("Content-Type", "")
        stripped = resp.text.strip()

        # 返回的是明码 JSON，如 {"code":"1234"}
        if stripped.startswith("{"):
            try:
                data = resp.json()
                number = str(data.get("code", data.get("data", ""))).strip()
            except (ValueError, AttributeError):
                number = resp.text.strip()
            self._captcha_number = number
            if number:
                self.logger.info("[验证码] 识别成功(明码): %r", number)
            return self._captcha_number

        # 返回的是图片
        if "image" in ctype or not stripped[:6].lower().startswith(("<!doct", "<html")):
            return self._solve_captcha_image(resp.content, C.GET_CODE_URL)

        # 返回 HTML：可能是新版把验证码内嵌在页面里
        image_bytes = extract_data_uri(resp.text)
        if image_bytes:
            self.logger.info("[验证码] 从 GetCode HTML 中提取到内嵌图片(len=%d)", len(image_bytes))
            return self._solve_captcha_image(image_bytes, C.GET_CODE_URL)

        self.logger.error(
            "[验证码] GetCode 返回 HTML 而非图片（疑似被拦截/接口已变更）：%.120s",
            stripped,
        )
        raise CaptchaUnavailableError(
            "验证码接口返回 HTML 页面，前置条件不满足（接口地址/请求头/Cookie/"
            "captcha_source_url 需校准）",
            body=resp.content,
        )

    def _fetch_embedded_captcha(self) -> bytes | None:
        """抓取 captcha_source_url 页面并提取内嵌验证码图片（data URI）。"""
        if not self.captcha_source_url:
            return None
        self.limiter.acquire()
        try:
            resp = self.session.get(
                self.captcha_source_url,
                headers={**self._headers, "Referer": C.HOME_URL},
                timeout=self.timeout,
                verify=self.verify,
            )
        except requests.RequestException as exc:
            self.logger.warning("[验证码] 抓取 captcha_source_url 失败：%s", exc)
            return None

        image_bytes = extract_data_uri(resp.text)
        if image_bytes:
            self.logger.info(
                "[验证码] 从 %s 提取到内嵌图片(len=%d)",
                self.captcha_source_url,
                len(image_bytes),
            )
            return image_bytes
        self.logger.debug(
            "[验证码] %s 未找到 data:image/jpg;base64 验证码",
            self.captcha_source_url,
        )
        return None

    def _solve_captcha_image(self, image_bytes: bytes, source: str) -> str:
        """把图片交给 captcha_solver；没有 solver 时抛 CaptchaRequiredError。"""
        self.logger.info("[验证码] 获取图片 len=%d source=%s", len(image_bytes), source)
        if self.captcha_solver is None:
            raise CaptchaRequiredError(
                "需要人工/第三方处理验证码", captcha_image=image_bytes
            )
        number = self.captcha_solver(image_bytes, source)
        if not number:
            raise CaptchaRequiredError(
                "验证码求解回调返回空", captcha_image=image_bytes
            )
        self._captcha_number = number.strip()
        return self._captcha_number

    def invalidate_captcha(self) -> None:
        """主动作废已缓存的验证码（下次请求将重新获取/求解）。"""
        self._captcha_number = None

    # ------------------------------------------------------------------ #
    # 登录（账号密码 + 验证码 + 3DES 加密密码）
    # ------------------------------------------------------------------ #
    def _load_env(self) -> None:
        """加载 .env 中的脱密凭据（包目录 wenshu_api/.env 与 cwd 均尝试）。

        不覆盖已存在的环境变量；凭据仅在运行时从 .env 读取，绝不硬编码。
        """
        here = os.path.dirname(os.path.abspath(__file__))
        # 先加载包自带 .env，再尝试当前工作目录 .env（dotenv 默认不覆盖已设变量）
        for cand in (os.path.join(here, ".env"), os.path.join(os.getcwd(), ".env")):
            if os.path.exists(cand):
                dotenv.load_dotenv(cand)

    def is_logged_in(self) -> bool:
        """返回当前是否已登录（login() 成功后置 True）。"""
        return self.logged_in

    def _inject_cookies(self, cookies) -> None:
        """把 Cookie 注入 requests.Session，使其携带已登录会话。

        :param cookies: dict(name->value) 或 list[dict]（Playwright/浏览器导出的
                        Cookie 列表，每项含 name/value/domain 等）。
                        若传入列表，自动过滤掉非 wenshu.court.gov.cn 域的 Cookie
                        （例如 OAuth 站点 account.court.gov.cn 的会话，注入会触发
                        服务端拒绝 code=9）。
        """
        if isinstance(cookies, dict):
            items = list(cookies.items())
        else:
            items = [
                (c.get("name"), c.get("value"))
                for c in cookies
                if "wenshu.court.gov.cn" in (c.get("domain") or "")
            ]
        for name, value in items:
            if not name:
                continue
            try:
                self.session.cookies.set(name, value, domain=".wenshu.court.gov.cn", path="/")
            except Exception:
                self.session.cookies.set(name, value)

    def _login_browser(self, username, password, cookies, **oauth_kwargs) -> dict:
        """浏览器后端登录：建立常驻 Playwright 上下文（OAuth 或注入 Cookie）。"""
        from .backend_browser import BrowserBackend

        if self._browser_backend is None:
            self._browser_backend = BrowserBackend(
                headless=oauth_kwargs.get("headless", True),
                timeout=self.timeout,
                captcha_solver=self.captcha_solver,
                session_state_path=oauth_kwargs.get("session_state_path"),
            )
        self._browser_backend.login(
            username=username, password=password, cookies=cookies,
            max_attempts=oauth_kwargs.get("max_attempts", 6),
            solve_mode=oauth_kwargs.get("solve_mode", "auto"),
            human_timeout=oauth_kwargs.get("human_timeout", 300),
        )
        self.logged_in = True
        self.logger.info("[登录] 浏览器后端模式：已建立常驻浏览器会话（绕过 wzws 软拦截）")
        return {
            "backend": "browser",
            "method": "inject" if cookies is not None else "oauth",
        }

    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cookies: Optional[object] = None,
        **oauth_kwargs,
    ) -> dict:
        """登录裁判文书网（OAuth 流程）。

        裁判文书网登录已改为 OAuth（account.court.gov.cn）。本方法支持两条路径：

        1. **注入会话（推荐 / 轻量 / CI）**：传入浏览器导出的已登录 Cookie
           （`cookies=` 为 dict 或 Playwright 的 Cookie 列表）。无需浏览器依赖，
           直接复用 SESSION。可从浏览器 DevTools Application → Cookies 复制
           wenshu.court.gov.cn 的 `SESSION` 等值，或用导出工具。
        2. **Playwright 自动 OAuth（默认）**：未传 `cookies` 时，若本机装有
           playwright + ddddocr，则驱动真实浏览器完成 OAuth 登录（含 ddddocr
           离线识别验证码），并把返回的 Cookie 注入会话。

        :param username/password: OAuth 凭据；为 None 时从 .env 读取
                                    （WENSHU_USER_NAME / WENSHU_PASSWORD）。
        :param cookies: 已登录会话 Cookie（dict 或 list），优先于此路径。
        :param oauth_kwargs: 透传给底层 OAuth 实现的参数（如 headless、max_attempts、
                             captcha_solver）。
        :return: {"cookies": {...}, "method": "inject" | "oauth"}。
        :raises WenshuError / RuntimeError: 凭据缺失、缺少 playwright 或登录失败时。
        """
        # 浏览器后端：登录逻辑交给常驻 Playwright 上下文（保持浏览器会话）
        if self._backend_mode == "browser":
            return self._login_browser(username, password, cookies, **oauth_kwargs)

        if cookies is not None:
            self._inject_cookies(cookies)
            self.logged_in = True
            self.logger.info("[登录] 已注入 %d 个 Cookie（注入会话路径）", len(cookies) if hasattr(cookies, "__len__") else 0)
            return {"cookies": dict(self.session.cookies), "method": "inject"}

        # 走 Playwright OAuth
        try:
            from .auth_oauth import oauth_login as _oauth_login
        except ImportError:
            raise WenshuError(
                "未提供 cookies= 且无法加载 OAuth 模块。请传入 cookies= 注入已登录会话，"
                "或确保 wenshu_api/auth_oauth.py 可导入。"
            )
        self.logger.info("[登录] 走 Playwright OAuth（凭据=%s）", username or os.getenv(C.ENV_USER_NAME))
        try:
            ck = _oauth_login(username=username, password=password, **oauth_kwargs)
        except Exception as e:  # noqa: BLE001
            raise WenshuError(f"OAuth 登录失败：{e}") from e
        self._inject_cookies(ck)
        self.logged_in = True
        self.logger.info("[登录] OAuth 成功，已注入 %d 个 Cookie", len(ck))
        return {"cookies": ck, "method": "oauth"}

    # ------------------------------------------------------------------ #
    # 会话复用（免重复点验证码）
    # ------------------------------------------------------------------ #
    def try_restore_session(self, probe_keyword: str = "合同",
                            page_size: int = 1) -> bool:
        """尝试复用上次落盘的会话快照，成功则跳过 OAuth（不必再人工点验证码）。

        为什么需要它：
          新版登录验证码是「点选文字」（tianai WORD_IMAGE_CLICK），无法离线自动
          识别，每次 OAuth 都要人工介入。而 SESSION 的有效期远长于单次进程的
          生命周期，把浏览器上下文快照（storage_state）落盘后，下次启动直接恢复
          即可，把「每次都要点验证码」降到「一天点一次」。

        流程：
          1) 恢复浏览器上下文并导航到搜索应用页；
          2) 发一次**极轻量**搜索（page_size=1）探活——只有真正命中数据才算
             复用成功（记忆中的软拦截特征：跨上下文重放会拿到 resultCount=0）；
          3) 探活失败则拆掉上下文、返回 False，由调用方回退正常 OAuth。

        :param probe_keyword: 探活用的关键词，取高频词以保证 resultCount > 0。
        :param page_size: 探活每页条数，保持 1 以最小化对站点的请求压力。
        :return: 是否成功复用（True 时 self.logged_in 已置为 True）。
        """
        if self._backend_mode != "browser":
            return False
        from .backend_browser import BrowserBackend

        if self._browser_backend is None:
            self._browser_backend = BrowserBackend(
                headless=True, timeout=self.timeout,
                captcha_solver=self.captcha_solver,
            )
        bb = self._browser_backend

        if not bb.restore():
            return False
        try:
            raw = self._search_raw(probe_keyword, [], page=1, page_size=page_size)
        except Exception as e:  # noqa: BLE001
            self.logger.info("[会话复用] 探活失败，回退 OAuth：%s",
                             f"{type(e).__name__}: {e}")
            bb.close()
            return False

        # 软拦截特征：能解密但 resultCount=0，等价于会话未被信任
        total = 0
        if isinstance(raw, dict):
            qr = raw.get("queryResult", raw)
            if isinstance(qr, dict):
                try:
                    total = int(qr.get("resultCount") or 0)
                except (TypeError, ValueError):
                    total = 0
        if total <= 0:
            self.logger.info("[会话复用] 探活返回 0 命中，判定快照不可用，回退 OAuth")
            bb.close()
            return False

        self.logged_in = True
        self.logger.info("[会话复用] 成功复用上次会话（探活命中 %d 条），已跳过验证码",
                         total)
        return True

    # ------------------------------------------------------------------ #
    # 网关请求（核心）
    # ------------------------------------------------------------------ #
    def _gateway_post(
        self,
        url: str,
        form_builder: Callable[[str, str, str], dict],
        request_uri: Optional[str] = None,
        response_check: Optional[Callable[[requests.Response], None]] = None,
    ) -> dict | list:
        """带验证码刷新重试的网关 POST（核心）。

        form_builder(number, vl5x, guid) -> POST 表单。
        response_check(resp): 可选，对响应做额外校验（如 404 -> DocumentNotFoundError），
                             在反爬解析之前调用。
        识别失败或服务端拒绝验证码时，会清空缓存、重新获取图片并重试，
        最多 self.max_captcha_retries 次；每次状态均写入日志。
        """
        self._ensure_session()
        last_exc: Exception | None = None

        for attempt in range(self.max_captcha_retries + 1):
            if attempt > 0:
                self.logger.warning(
                    "[验证码] 刷新并重试（第 %d/%d 次）", attempt, self.max_captcha_retries
                )
                self._captcha_number = None

            # 1) 获取/识别验证码（缓存命中则跳过图片获取）
            try:
                number = self._get_code()
            except CaptchaRequiredError as e:
                self.logger.warning(
                    "[验证码] 识别失败，刷新重试（第 %d/%d 次）: %s",
                    attempt + 1, self.max_captcha_retries, e,
                )
                last_exc = e
                continue

            vl5x = get_vl5x(self.vjkl5)
            guid = random_guid()
            form = form_builder(number, vl5x, guid)

            self.limiter.acquire()
            self.logger.debug("[请求] POST %s", url)
            try:
                resp = self.session.post(
                    url, data=form, headers=self._headers,
                    timeout=self.timeout, verify=self.verify,
                )
            except requests.RequestException as exc:
                raise NetworkError(f"网关请求失败：{exc}") from exc

            if response_check is not None:
                response_check(resp)

            try:
                return self._handle_response(resp, request_uri or url)
            except CaptchaRequiredError as e:
                # 服务端拒绝该验证码 -> 清空缓存，进入下一轮刷新
                self._captcha_number = None
                last_exc = e
                continue

        # 超出最大刷新次数
        self.logger.error(
            "[验证码] 已达到最大刷新次数 %d，仍失败", self.max_captcha_retries
        )
        raise last_exc or CaptchaRequiredError("验证码多次刷新仍被拒绝")

    @retry(max_retries=3)
    def _post_gateway(self, request_uri: str, param: dict) -> dict | list:
        """向网关发起一次查询（包装 _gateway_post）。"""

        def build(number, vl5x, guid):
            form = {
                "guid": guid,
                "number": number,
                "vjkl5": self.vjkl5,
                "vl5x": vl5x,
                "requestUri": request_uri,
            }
            if self.ciphertext_generator is not None:
                form["ciphertext"] = self.ciphertext_generator(param)
            else:
                form["param"] = json.dumps(param, ensure_ascii=False)
            return form

        return self._gateway_post(C.GATEWAY_URL, build, request_uri)

    def _handle_response(self, resp: requests.Response, request_uri: str):
        """解析网关响应，识别反爬信号并抛出对应异常。"""
        text = resp.text

        # 1) HTTP 层频率限制
        if resp.status_code in (429, 503):
            retry_after = int(resp.headers.get("Retry-After", 60))
            self.logger.warning(
                "[限流] HTTP %d，建议等待 %ds", resp.status_code, retry_after
            )
            raise RateLimitError(
                f"被频率限制（HTTP {resp.status_code}）", retry_after=retry_after
            )

        # 2) 业务层验证码/封锁特征（不同版本文案可能不同，集中在此扩充）
        block_signals = ("验证码", "滑动", "验证失败", "操作过于频繁", "请先验证")
        if any(sig in text for sig in block_signals):
            # 验证失败：作废验证码缓存与会话，下次请求会重新获取/求解
            self.logger.warning("[验证码] 服务端返回验证要求（拒绝当前验证码）")
            self._captcha_number = None
            self._initialized = False
            raise CaptchaRequiredError("网关返回验证码/验证要求，请处理验证码后重试。")

        if resp.status_code != 200:
            raise NetworkError(f"网关返回 HTTP {resp.status_code}: {text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            # 某些版本返回的是裸数组或包裹字符串
            cleaned = text.strip()
            if cleaned.startswith("[") or cleaned.startswith("{"):
                try:
                    data = json.loads(cleaned)
                except ValueError:
                    raise ParseError(f"响应非 JSON：{text[:200]}") from exc
            else:
                raise ParseError(f"无法解析响应：{text[:200]}") from exc

        # 3) JSON 层面的错误标记
        if isinstance(data, dict):
            if data.get("code") in ("0", 0) and not data.get("data") and data.get("message"):
                msg = data.get("message", "")
                if any(s in msg for s in block_signals):
                    raise CaptchaRequiredError(msg)
                raise ParseError(f"业务错误：{msg}")
        return data

    # ------------------------------------------------------------------ #
    # 功能 1 + 3：关键词组合查询 / 结果列表
    # ------------------------------------------------------------------ #
    def _ensure_login(self) -> None:
        """确保已登录；未登录则提示先调用 login()。

        浏览器后端模式下，首次需要登录时自动发起 OAuth（保持浏览器会话），
        让 client.search() / get_document_content() 开箱即用。
        """
        if self.logged_in:
            return
        if self._backend_mode == "browser":
            self.login()
            return
        raise SessionExpiredError(
            "尚未登录。请先调用 client.login()（注入 Cookie 或走 Playwright OAuth）"
            "再进行搜索/下载。"
        )

    def _raw_post(self, url: str, form: dict, referer: str) -> requests.Response:
        """底层 POST：限流 + 超时 + 频率限制处理，返回原始响应。"""
        self.limiter.acquire()
        headers = {**self._headers, "Referer": referer}
        try:
            resp = self.session.post(
                url, data=form, headers=headers,
                timeout=self.timeout, verify=self.verify,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"网关请求失败：{exc}") from exc
        if resp.status_code in (429, 503):
            retry_after = int(resp.headers.get("Retry-After", 60))
            self.logger.warning("[限流] HTTP %d，建议等待 %ds", resp.status_code, retry_after)
            raise RateLimitError(f"被频率限制（HTTP {resp.status_code}）", retry_after=retry_after)
        if resp.status_code != 200:
            raise NetworkError(f"网关返回 HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def _gateway_text(self, url: str, form: dict, referer: str) -> str:
        """统一的网关请求入口：按后端模式分派，返回响应 JSON 信封文本。

        - requests 后端：走 _raw_post（限流 + 超时 + 频率限制），返回 resp.text。
        - browser  后端：在常驻 Playwright 上下文内 fetch（绕过 wzws 软拦截），
          返回浏览器内 JSON 信封文本。

        ★这是所有网关请求的统一出口，故在这里做最后一次表单清洗（剔除 None，
        详见 _clean_form）。任何调用方构造表单时漏掉的 None，都不会被浏览器后端
        编码成字符串 "null" 发出去——那正是 code=9「JSONNull cannot be cast to
        java.lang.String」的成因（2026-08-30 真机抓包定位）。
        """
        form = self._clean_form(form)
        if self._backend_mode == "browser" and self._browser_backend is not None:
            self.limiter.acquire()
            self.logger.debug("[浏览器后端] POST %s", url)
            try:
                text = self._browser_backend.fetch_gateway(form, referer)
            except Exception as e:  # noqa: BLE001
                raise NetworkError(f"浏览器后端请求失败：{e}") from e
            if isinstance(text, str) and text.startswith("ERR:"):
                raise NetworkError(f"浏览器内 fetch 失败：{text}")
            return text

        resp = self._raw_post(url, form, referer=referer)
        return resp.text

    def _decrypt_payload(self, resp) -> object:
        """解析搜索网关响应：处理 code 与 3DES 加密的 result 字段。

        兼容 requests.Response 与原始文本（浏览器后端直接返回 JSON 信封文本）。
        """
        if isinstance(resp, str):
            text = resp
        else:
            text = resp.text
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"响应非 JSON：{text[:200]}") from exc

        code = payload.get("code")
        if code not in (  1, "1", "success"):
            desc = payload.get("description") or payload.get("message") or ""
            if any(s in desc for s in ("验证码", "验证失败", "操作过于频繁", "请先验证")):
                raise CaptchaRequiredError(desc or "网关要求验证")
            # code=9 “没有权限请求接口” 通常是登录态 SESSION 过期/失效
            if code in (9, "9") or "没有权限" in desc or "权限" in desc:
                self.logged_in = False
                raise SessionExpiredError(
                    f"登录态已失效（code={code}，{desc}）。请重新 client.login()。"
                    f" 原始回包前120字：{text[:120]}"
                )
            raise WenshuError(f"搜索失败：code={code} desc={desc} 原始回包前120字：{text[:120]}")

        # 新版响应：result 用 secretKey 作 3DES 密钥、iv=当天日期 加密
        if payload.get("secretKey") and payload.get("result"):
            try:
                text = des3_decrypt(payload["result"], payload["secretKey"])
                return json.loads(text)
            except (ValueError, KeyError) as exc:
                raise ParseError(f"解密响应失败：{exc}") from exc

        # 兼容未加密形态
        return payload.get("result", payload)

    def _build_query_condition(
        self,
        keyword: Optional[str] = None,
        cause: Optional[str] = None,
        court_name: Optional[str] = None,
        case_type: Optional[str] = None,
        trial_procedure: Optional[str] = None,
    ) -> list:
        """把人类可读的查询条件拼成 queryCondition 列表。"""
        conditions = []
        mapping = {
            "keyword": keyword,
            "cause": cause,
            "court_name": court_name,
            "case_type": case_type,
            "trial_procedure": trial_procedure,
        }
        for field_name, value in mapping.items():
            if value:
                key = C.FIELD_KEYS[field_name]
                if field_name == "case_type" and value in C.CASE_TYPES:
                    value = C.CASE_TYPES[value]
                conditions.append({"key": key, "value": value})
        return conditions

    def _search_raw(
        self,
        keyword: str,
        conditions: list,
        page: int = 1,
        page_size: int = C.DEFAULT_PAGE_SIZE,
        sort: Optional[str] = None,
    ) -> object:
        """向搜索网关发起 queryDoc 请求并解密返回。

        表单字段逆向自认证态搜索应用 JS（2026-07 实测）：pageId / s21 / sortFields /
        ciphertext / pageNum / pageSize / queryCondition / cfg / __RequestVerificationToken
        / wh / ww / cs。Referer 带 pageId 与关键词，与真实请求一致。

        ★``sort`` 兜底（2026-08-30 真机抓包定位的 code=9 根因，务必保留）：
          ``sortFields`` 是**必填字符串**字段。若这里收到 None，浏览器后端会用
          ``encodeURIComponent(null)`` 把它编码成字面量 ``"null"`` 发出去（requests
          后端则会直接跳过该字段，行为不一致），站点 net.sf.json 解析成 JSONNull 后
          再 cast 到 String 即抛异常并以 code=9 拒绝整个请求：

            {"code":9,"description":"net.sf.json.JSONNull cannot be cast to
             java.lang.String",...}

          这个错误信息**逐字指向** sortFields，而不是什么模糊的风控。故此处统一
          兜底为 C.DEFAULT_SORT，绝不让 None 流入表单。
        """
        sort = sort or C.DEFAULT_SORT
        # 浏览器后端由 BrowserBackend 常驻管理，过期自愈由 AgentSession._with_relogin
        # 负责；requests 后端则需先确保已登录（含凭据 / 注入 Cookie）。
        if self._backend_mode != "browser":
            self._ensure_login()
        # Referer 需带 pageId 与当前关键词（与站点真实请求一致）
        from urllib.parse import quote
        referer = f"{C.SEARCH_APP_URL}?pageId={C.PAGE_ID}&s21={quote(keyword or '')}"
        form = {
            "pageId": C.PAGE_ID,
            "s21": keyword or "",
            "sortFields": sort,
            "ciphertext": generate_ciphertext(),
            "pageNum": str(page),
            "pageSize": str(page_size),
            "queryCondition": json.dumps(conditions, ensure_ascii=False),
            "cfg": C.SEARCH_CFG,
            # __RequestVerificationToken 是前端 base.random(24) 生成的随机串（非服务端下发）
            "__RequestVerificationToken": wenshu_random(24),
            "wh": "720", "ww": "1280", "cs": "0",
        }
        self.logger.debug("[请求] POST queryDoc keyword=%r page=%d", keyword, page)
        text = self._gateway_text(C.GATEWAY_URL, self._clean_form(form),
                                  referer=referer)
        return self._decrypt_payload(text)

    def _clean_form(self, form: dict) -> dict:
        """剔掉值为 None 的表单字段。

        两个后端的编码行为不一致，是踩过坑的地方：
          * requests 后端：``data={k: None}`` 会跳过该字段（不出现在请求体里）；
          * 浏览器后端：``encodeURIComponent(null)`` 会变成**字符串 "null"**。

        也就是说同一个 None 在浏览器后端下会变成一个真实存在的、值为 "null" 的
        字段，站点 net.sf.json 解析成 JSONNull 后再 cast 到 String 就抛
        「JSONNull cannot be cast to java.lang.String」→ code=9。
        这里统一剔除并告警，避免同类问题再次变成难查的 code=9。
        """
        bad = [k for k, v in form.items() if v is None]
        if bad:
            self.logger.warning(
                "[请求] 表单含 None 字段，已剔除（否则浏览器后端会编码成字符串 'null'）：%s",
                bad)
        return {k: v for k, v in form.items() if v is not None}

    def search(
        self,
        keyword: Optional[str] = None,
        cause: Optional[str] = None,
        court_name: Optional[str] = None,
        case_type: Optional[str] = None,
        trial_procedure: Optional[str] = None,
        page: int = 1,
        page_size: int = C.DEFAULT_PAGE_SIZE,
        sort: Optional[str] = None,
    ) -> SearchResult:
        """功能 1：关键词组合查询（同时覆盖功能 3 的分页列表）。

        命中真实搜索网关 `/website/parse/rest.q4w`（cfg=SearchDataDsoDTO@queryDoc），
        需已登录会话（login() 成功后）。反爬令牌 ciphertext 与请求校验均由本库自动完成。

        参数：
            keyword:          全文检索关键词（如“买卖合同”），映射到字段 s21。
            cause:            案由（如“民间借贷纠纷”）。
            court_name:       法院名称（如“最高人民法院”）。
            case_type:        案件类型，可传中文或枚举（刑/民/行/赔/执）。
            trial_procedure:  审判程序（如“一审”“二审”）。
            page:             页码，从 1 开始。
            page_size:        每页条数（站点通常 5/10/20）。
            sort:             排序字段；None（默认）表示用库默认 s50:desc。
                              ★切勿显式传 None 以外的空值：sortFields 是必填
                              字符串字段，空值会被编码成 "null" 触发 code=9。

        返回：SearchResult（total / page / documents）。
        """
        conditions = self._build_query_condition(
            keyword=keyword, cause=cause, court_name=court_name,
            case_type=case_type, trial_procedure=trial_procedure,
        )
        raw = self._search_raw(
            keyword or "", conditions, page=page, page_size=page_size, sort=sort
        )
        return self._parse_list(raw, page, page_size)

    def list_documents(
        self,
        query_condition: Iterable[dict],
        page: int = 1,
        page_size: int = C.DEFAULT_PAGE_SIZE,
        sort: Optional[str] = None,
    ) -> SearchResult:
        """功能 3（底层）：直接传入 queryCondition 列表进行查询。

        query_condition 形如 [{"key": "s21", "value": "借款"}, ...]，
        适合需要精细控制字段键名的场景。
        """
        conditions = [dict(c) for c in query_condition]
        # 从条件里推断 s21（全文关键词），保持与站点一致
        s21 = ""
        for c in conditions:
            if c.get("key") == "s21":
                s21 = c.get("value", "")
                break
        raw = self._search_raw(s21, conditions, page=page, page_size=page_size, sort=sort)
        return self._parse_list(raw, page, page_size)

    def _parse_list(self, raw: object, page: int, page_size: int) -> SearchResult:
        """把解密后的搜索响应解析为 SearchResult。

        2026-07 实测响应结构：
            {
              "relWenshu": {...},
              "queryParams": {...},
              "queryResult": {
                "resultCount": <int 命中总数>,
                "resultList": [ { 数值字段: 值, "rowkey": <文书标识> }, ... ]
              }
            }
        列表项字段为编号键（与站点 s1..sN 对外字段一致）：
            "1"=案件名称 "2"=法院名称 "7"=案号 "31"=日期
            "26"=本院认为/裁判要旨 "rowkey"=文书唯一标识（拉详情/下载用）
        """
        if not isinstance(raw, dict):
            raise ParseError(f"无法识别的列表响应类型：{type(raw)}")

        # 优先取 queryResult 包裹；兼容直接给 resultList 的形态
        qr = raw.get("queryResult", raw)
        if not isinstance(qr, dict):
            raise ParseError(f"queryResult 不是对象：{type(qr)}")
        items = qr.get("resultList") or []
        if not isinstance(items, list):
            items = [items]
        total = 0
        rc = qr.get("resultCount")
        if isinstance(rc, (int, str)) and str(rc).isdigit():
            total = int(rc)

        documents: list[DocumentMeta] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            doc = DocumentMeta(raw=it)
            doc.rowkey = (it.get("rowkey") or it.get("44") or "").strip()
            doc.doc_id = doc.rowkey
            doc.title = _strip_html(it.get("1") or "").strip()
            doc.court_name = (it.get("2") or "").strip()
            doc.case_number = (it.get("7") or "").strip()
            doc.publish_date = (it.get("31") or "").strip()
            doc.summary = _strip_html(it.get("26") or "").strip()
            # 兼容对外 s1..sN 字段（万一站点切换回旧结构）
            for src_key, attr in _LIST_FIELD_MAP.items():
                val = it.get(src_key)
                if val and not getattr(doc, attr):
                    setattr(doc, attr, str(val).strip())
            if doc.doc_id:
                doc.doc_url = f"{C.BASE_URL}/website/wenshu/{doc.doc_id}.html"
            documents.append(doc)

        # 响应未给总数时，用当前页条数兜底（仅当前页可信）
        if total == 0:
            total = len(documents) if page == 1 else page * page_size

        return SearchResult(
            total=total, page=page, page_size=page_size, documents=documents
        )

    # ------------------------------------------------------------------ #
    # 功能 2：数据库结构获取
    # ------------------------------------------------------------------ #
    def get_db_structure(self) -> DatabaseStructure:
        """功能 2：返回公开数据库结构概览（可查询字段/案件类型/法院层级/案由示例）。"""
        fields = [
            FieldMeta("s8", "全文检索关键词", "合同纠纷"),
            FieldMeta("ay", "案由", "民间借贷纠纷"),
            FieldMeta("court", "法院名称", "最高人民法院"),
            FieldMeta("docType", "案件类型", "民事案件 / ms"),
            FieldMeta("spcx", "审判程序", "一审"),
            FieldMeta("s50", "发布日期", "2024-01-01"),
        ]
        return DatabaseStructure(
            queryable_fields=fields,
            case_types=dict(C.CASE_TYPES),
            court_levels=list(C.COURT_LEVELS),
            cause_examples=list(C.COMMON_CAUSES),
            note=(
                "完整法院树/案由树建议运行时调用 get_court_tree() / get_cause_tree() "
                "从站点拉取；站点接口可能变动，详见 README。"
            ),
        )

    def get_case_types(self) -> dict[str, str]:
        """返回案件类型枚举（中文 -> 代码）。"""
        return dict(C.CASE_TYPES)

    def get_court_levels(self) -> list[str]:
        """返回法院层级列表。"""
        return list(C.COURT_LEVELS)

    def get_court_tree(self) -> CourtNode:
        """获取法院层级结构树（尽力从站点拉取，失败则回退本地结构）。

        返回根节点，children 为各层级/专门法院。
        """
        try:
            raw = self._post_gateway(
                "/website/query/getTreeList",
                {"treeType": "court"},
            )
            return self._parse_tree(raw, root_name="中国法院")
        except (NetworkError, ParseError, CaptchaRequiredError):
            # 回退：网络/解析失败或触发验证码时，用本地常量构建扁平结构
            root = CourtNode(name="中国法院")
            for lvl in C.COURT_LEVELS:
                root.children.append(CourtNode(name=lvl))
            return root

    def get_cause_tree(self) -> CourtNode:
        """获取案由分类树（尽力从站点拉取，失败则回退本地示例）。"""
        try:
            raw = self._post_gateway(
                "/website/query/getTreeList",
                {"treeType": "cause"},
            )
            return self._parse_tree(raw, root_name="案由")
        except (NetworkError, ParseError, CaptchaRequiredError):
            root = CourtNode(name="案由")
            for cause in C.COMMON_CAUSES:
                node = root
                for part in cause.split(">"):
                    part = part.strip()
                    child = next((c for c in node.children if c.name == part), None)
                    if child is None:
                        child = CourtNode(name=part)
                        node.children.append(child)
                    node = child
            return root

    @staticmethod
    def _parse_tree(raw: dict | list, root_name: str) -> CourtNode:
        """把站点树形响应递归解析为 CourtNode。"""
        root = CourtNode(name=root_name)
        items = raw if isinstance(raw, list) else raw.get("data") or raw.get("list") or []

        def build(nodes: list) -> list[CourtNode]:
            result = []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                name = n.get("name") or n.get("text") or n.get("label") or ""
                code = str(n.get("id") or n.get("code") or "")
                node = CourtNode(name=str(name), code=code)
                children = n.get("children") or n.get("child") or []
                if children:
                    node.children = build(children)
                result.append(node)
            return result

        root.children = build(items)
        return root

    # ------------------------------------------------------------------ #
    # 功能 4：文书下载（docInfoSearch 协议，2026-07 实测）
    # ------------------------------------------------------------------ #
    @retry(max_retries=2)
    def get_document_content(self, doc_id: str) -> DocumentContent:
        """功能 4（取内容）：根据 docId 获取文书全文。

        命中真实网关 `/website/parse/rest.q4w`，cfg=SearchDataDsoDTO@docInfoSearch。
        需已登录会话（login() 成功后）+ ciphertext 反爬令牌 + docId（= 搜索结果
        rowkey）。响应用 secretKey 作 3DES 密钥、iv=当天日期解密，得到结构化 JSON。

        参数：
            doc_id: 文书 ID（来自搜索结果 DocumentMeta.doc_id / rowkey）。

        返回：DocumentContent（结构化字段 + full_text 纯文本 + html 完整 HTML）。
        """
        self._ensure_login()
        referer = C.detail_referer(doc_id)
        form = {
            "docId": doc_id,
            "ciphertext": generate_ciphertext(),
            "cfg": C.DOC_INFO_CFG,
            # docInfoSearch **无** pageId；__RequestVerificationToken 同搜索为前端随机串
            "__RequestVerificationToken": wenshu_random(24),
            "wh": "720", "ww": "1280", "cs": "0",
        }
        self.logger.debug("[请求] POST docInfoSearch docId=%s…", doc_id[:20])
        text = self._gateway_text(C.GATEWAY_URL, form, referer=referer)
        raw = self._decrypt_docinfo(text, doc_id)
        return self._parse_document(raw, doc_id)

    def _decrypt_docinfo(self, resp, doc_id: str) -> dict:
        """解析 docInfoSearch 响应：处理 code 与 3DES 加密的 result 字段。

        兼容 requests.Response 与原始文本（浏览器后端直接返回 JSON 信封文本）。
        """
        if isinstance(resp, str):
            text = resp
        else:
            text = resp.text
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"响应非 JSON：{text[:200]}") from exc

        code = payload.get("code")
        if code not in (1, "1", "success"):
            desc = payload.get("description") or payload.get("message") or ""
            if any(s in desc for s in ("验证码", "验证失败", "操作过于频繁", "请先验证")):
                raise CaptchaRequiredError(desc or "网关要求验证")
            # code=9 “没有权限请求接口” 通常是登录态 SESSION 过期/失效
            if code in (9, "9") or "没有权限" in desc or "权限" in desc:
                self.logged_in = False
                raise SessionExpiredError(
                    f"登录态已失效（code={code}，{desc}）。请重新 client.login()。"
                )
            # 业务层“文书不存在/无权访问”转 DocumentNotFoundError
            if any(s in desc for s in ("不存在", "未找到", "无权", "没有该", "已下线")):
                raise DocumentNotFoundError(f"文书不存在或无权访问：{doc_id}（{desc}）")
            raise WenshuError(f"获取文书失败：code={code} desc={desc}")

        if payload.get("secretKey") and payload.get("result"):
            try:
                text = des3_decrypt(payload["result"], payload["secretKey"])
                return json.loads(text)
            except (ValueError, KeyError) as exc:
                raise ParseError(f"解密文书失败：{exc}") from exc

        # 兼容未加密形态（极少）
        return payload.get("result", payload)

    @staticmethod
    def _parse_document(raw: object, doc_id: str) -> DocumentContent:
        """把 docInfoSearch 解密后的 JSON 解析为 DocumentContent。

        结构化字段（逆向自 docInfoSearch 解密体，2026-07 实测）：
            s1 案件名称  s2 法院  s7 案号  s8 案件类型  s9 审判程序
            s31 发布日期  s41 裁判日期
            s22 标题区  s23 案件由来/当事人  s25 诉辩意见
            s26 本院认为  s27 判决主文  s28 尾部署名
            s11 案由[]  s45 关键词[]  s47 法律依据[]  qwContent 完整渲染 HTML
        """
        if not isinstance(raw, dict):
            raise DocumentNotFoundError(f"无法识别的文书响应类型：{type(raw)}（{doc_id}）")
        # 命中/权限异常兜底：解密体为空或既无标题也无 HTML，视为文书不存在
        if not raw.get("s1") and not raw.get("qwContent"):
            raise DocumentNotFoundError(f"文书不存在或无权访问：{doc_id}")

        # 正文按阅读顺序拼接结构化字段（纯文本，干净无防伪字距）
        segments = [raw.get(k, "") for k in ("s22", "s23", "s25", "s26", "s27", "s28")]
        full_text = "\n\n".join(s for s in segments if s).strip()

        # 案由：s11 为列表，取首项
        cause_val = raw.get("s11") or []
        if isinstance(cause_val, list) and cause_val:
            cause = str(cause_val[0])
        elif isinstance(cause_val, str):
            cause = cause_val
        else:
            cause = ""

        # 法律依据：s47 为 [{tkx, fgmc, fgid}]
        legal: list[LegalBasis] = []
        for lb in (raw.get("s47") or []):
            if isinstance(lb, dict):
                legal.append(LegalBasis(
                    clause=(lb.get("tkx") or "").strip(),
                    law_name=(lb.get("fgmc") or "").strip(),
                    law_id=(lb.get("fgid") or "").strip(),
                ))

        return DocumentContent(
            doc_id=doc_id,
            title=(raw.get("s1") or "").strip(),
            court_name=(raw.get("s2") or "").strip(),
            case_number=(raw.get("s7") or "").strip(),
            case_type=(raw.get("s8") or "").strip(),
            trial_procedure=(raw.get("s9") or "").strip(),
            publish_date=(raw.get("s31") or "").strip(),
            judgment_date=(raw.get("s41") or "").strip(),
            cause=cause,
            keywords=list(raw.get("s45") or []),
            legal_basis=legal,
            title_block=(raw.get("s22") or "").strip(),
            background=(raw.get("s23") or "").strip(),
            claims=(raw.get("s25") or "").strip(),
            court_opinion=(raw.get("s26") or "").strip(),
            judgment_result=(raw.get("s27") or "").strip(),
            signatures=(raw.get("s28") or "").strip(),
            html=(raw.get("qwContent") or ""),
            view_count=(raw.get("viewCount") or "").strip(),
            full_text=full_text,
            raw=raw,
        )

    def download_document(
        self,
        doc_id: str,
        save_format: str = "text",
        save_path: Optional[str] = None,
        encoding: str = "utf-8",
    ) -> str:
        """功能 4（落盘）：下载文书并保存。

        参数：
            doc_id:       文书 ID（来自搜索结果 DocumentMeta.doc_id）。
            save_format:  "text" 保存纯文本(.txt)；“html” 保存完整渲染 HTML(.html)；
                          "pdf" 用可用库把 HTML 转 PDF(.pdf)。
            save_path:    保存路径或目录；为 None 时保存到当前目录。
            encoding:     文本编码，默认 utf-8（“html” 始终 utf-8）。

        返回：实际保存的文件路径。
        """
        doc = self.get_document_content(doc_id)

        if save_path is None:
            save_path = os.path.join(os.getcwd(), f"{doc_id}.{_ext(save_format)}")
        elif os.path.isdir(save_path):
            save_path = os.path.join(save_path, f"{doc_id}.{_ext(save_format)}")
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        if save_format == "text":
            with open(save_path, "w", encoding=encoding) as f:
                f.write(doc.full_text)
            return save_path

        if save_format == "html":
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(doc.html)
            return save_path

        if save_format == "pdf":
            return _save_as_pdf(doc.html, save_path, doc.full_text)

        if save_format == "docx":
            return _save_as_docx(doc, save_path)

        raise ValueError(f"不支持的 save_format：{save_format}（仅 text / html / pdf / docx）")

    # ------------------------------------------------------------------ #
    # 上下文管理
    # ------------------------------------------------------------------ #
    def reset_session(self) -> None:
        """重置会话：关闭浏览器后端（若启用）、清空登录态与验证码缓存并重建会话。"""
        if self._browser_backend is not None:
            self._browser_backend.close()
            self._browser_backend = None
        self.logged_in = False
        self._initialized = False
        self._captcha_number = None
        self.vjkl5 = ""
        try:
            self.session.cookies.clear()
        except Exception:  # noqa: BLE001
            pass
        self.init_session()

    def close(self) -> None:
        if self._browser_backend is not None:
            try:
                self._browser_backend.close()
            finally:
                self._browser_backend = None
        self.session.close()

    def __enter__(self) -> "WenshuClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------- #
# 模块级辅助函数
# ---------------------------------------------------------------------- #
def _ext(fmt: str) -> str:
    return {"text": "txt", "html": "html", "pdf": "pdf", "docx": "docx"}.get(fmt, fmt)


def _html_to_plain(html_text: str) -> str:
    """极简 HTML -> 纯文本（去 script/style、去标签、解码实体、合并空白）。"""
    if not html_text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _save_as_pdf(html_text: str, path: str, full_text: Optional[str] = None) -> str:
    """把文书转成 PDF 并保存。

    转换后端优先级（自动择优，无需用户干预）：
      1. weasyprint        —— CSS 还原度最高（需系统 cairo/pango 原生库）。
      2. pdfkit            —— 需系统安装 wkhtmltopdf。
      3. reportlab（兜底）  —— 纯 Python，内置 STSong-Light 中文字体，
                               **无需任何外部字体或原生库**，中文必定可渲染。
                               用结构化 full_text（无防伪字距）排版，阅读体验最佳。

    每个后端若 import 或运行时失败（如 weasyprint 缺 libgobject 原生库），
    均自动回退到下一个；全部不可用则退化为保存 .html 并抛出明确错误。
    """
    errors = []

    # 1) weasyprint：HTML/CSS 还原度最佳
    try:
        import weasyprint  # type: ignore

        weasyprint.HTML(string=html_text).write_pdf(path)
        return path
    except Exception as e:  # 含 ImportError 与缺原生库的 OSError/RuntimeError
        errors.append(f"weasyprint: {type(e).__name__}: {e}")

    # 2) pdfkit：需要 wkhtmltopdf 二进制
    try:
        import pdfkit  # type: ignore

        pdfkit.from_string(html_text, path)
        return path
    except Exception as e:
        errors.append(f"pdfkit: {type(e).__name__}: {e}")

    # 3) reportlab 纯 Python 兜底（内置 STSong-Light 中文 CID 字体）
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_JUSTIFY
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        body = full_text or _html_to_plain(html_text)

        doc = SimpleDocTemplate(
            path, pagesize=A4,
            leftMargin=2 * cm, rightMargin=2 * cm,
            topMargin=2 * cm, bottomMargin=2 * cm,
            title="裁判文书",
        )
        style = ParagraphStyle(
            "body", fontName="STSong-Light", fontSize=11, leading=19,
            alignment=TA_JUSTIFY, spaceAfter=6, firstLineIndent=22,
        )
        story = []
        for block in body.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            esc = (block.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;"))
            story.append(Paragraph(esc, style))
            story.append(Spacer(1, 4))
        doc.build(story)
        return path
    except Exception as e:
        errors.append(f"reportlab: {type(e).__name__}: {e}")

    html_path = os.path.splitext(path)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    raise RuntimeError(
        "所有 PDF 生成后端均不可用（" + "; ".join(errors) +
        f"）。已退化为保存 HTML：{html_path}。推荐 `pip install reportlab`（纯 Python）。"
    )


def _save_as_docx(doc, path: str) -> str:
    """把结构化文书转成 Word(.docx) 并保存（python-docx，纯 Python、跨平台）。

    排版：标题 → 元信息表（法院/案号/类型/程序/日期/案由）→ 正文结构化段落
    （案件由来/诉辩意见/本院认为/判决主文/署名）→ 关键词 → 法律依据。
    纯 Python，无需原生库；Windows/macOS/Linux 通用。
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"生成 DOCX 需要 python-docx：{e}。请 `pip install python-docx`。"
        ) from e

    document = Document()

    if doc.title:
        document.add_heading(doc.title, level=0)

    # 元信息表
    meta = []
    if doc.court_name:
        meta.append(("法院", doc.court_name))
    if doc.case_number:
        meta.append(("案号", doc.case_number))
    if doc.case_type:
        meta.append(("案件类型", doc.case_type))
    if doc.trial_procedure:
        meta.append(("审判程序", doc.trial_procedure))
    if doc.publish_date:
        meta.append(("发布日期", doc.publish_date))
    if doc.judgment_date:
        meta.append(("裁判日期", doc.judgment_date))
    if doc.cause:
        meta.append(("案由", doc.cause))
    if meta:
        table = document.add_table(rows=0, cols=2)
        table.style = "Light Grid Accent 1"
        for k, v in meta:
            cells = table.add_row().cells
            cells[0].text = k
            cells[1].text = v
        document.add_paragraph("")

    # 正文结构化段落（标签 + 内容）
    blocks = [
        (None, doc.title_block),
        ("案件由来 / 当事人", doc.background),
        ("诉辩意见", doc.claims),
        ("本院认为", doc.court_opinion),
        ("判决主文", doc.judgment_result),
        (None, doc.signatures),
    ]
    for label, text in blocks:
        if not text:
            continue
        if label:
            document.add_heading(label, level=1)
        for para in text.split("\n"):
            para = para.strip()
            if para:
                document.add_paragraph(para)

    if doc.keywords:
        document.add_heading("关键词", level=1)
        document.add_paragraph("、".join(doc.keywords))

    if doc.legal_basis:
        document.add_heading("法律依据", level=1)
        for lb in doc.legal_basis:
            line = f"{lb.clause} {lb.law_name}".strip()
            if line:
                document.add_paragraph(line)

    document.save(path)
    return path
