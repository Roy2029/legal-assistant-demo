"""浏览器后端：保持一个已登录的 Playwright 浏览器上下文，search/download 经
page.evaluate(fetch ...) 在浏览器上下文内发请求，从而绕过 wzws 防火墙的跨上下文
软拦截（见 README 第 5 节）。响应回到 Python 后照常 3DES 解密。

为什么需要它：
  裁判文书网的 wzws 防火墙会把「校验通过」绑定在当初通过挑战的浏览器会话上。
  用 requests 把 SESSION Cookie 跨进程重放时，即使 Cookie 完全正确，网关也会
  返回 code=1 但 resultCount=0 的空结果（软拦截）。只有「请求来自受信任的浏览器
  上下文」时才能命中真实数据。浏览器后端就是把网关请求放在常驻浏览器里发，从而
  稳定绕过这一限制。

用法：
    client = WenshuClient(backend="browser")
    client.login()                         # 走 OAuth，浏览器常驻（自动）
    # 或：client.login(cookies=<SESSION>)  # 注入已登录 SESSION 到浏览器上下文
    res = client.search("买卖合同")          # 请求在浏览器内发出，绕过软拦截
    doc = client.get_document_content(rowkey)
    client.close()                         # 关闭浏览器，释放资源
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from . import constants as C
from .exceptions import WenshuError


def default_state_path() -> str:
    """会话快照的默认落盘位置：~/.wenshu/browser_session.json。

    放在用户目录而非包目录，避免随包发布/重装被覆盖，也便于权限隔离
    （该文件等价于一个已登录会话，含 SESSION Cookie）。
    """
    env = os.getenv("WENSHU_SESSION_STATE")
    if env:
        return env
    home = os.path.expanduser("~")
    return os.path.join(home, ".wenshu", "browser_session.json")


class BrowserBackend:
    """管理一个常驻 Playwright 浏览器上下文，提供浏览器内 fetch 网关请求。

    线程安全性：本类非线程安全，应与单一 WenshuClient 实例配合、串行使用。
    """

    def __init__(self, headless: bool = True, timeout: int = 30,
                 captcha_solver=None, session_state_path: Optional[str] = None):
        self.headless = headless
        self.timeout = timeout
        self.captcha_solver = captcha_solver
        self.session_state_path = session_state_path or default_state_path()
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._trusted = False
        # 会话来源：oauth（刚走完验证码）/ restored（复用快照）/ inject（注入 Cookie）
        self.session_source: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _ensure_browser(self) -> None:
        """惰性启动 Playwright 浏览器（首次 login 时调用）。"""
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # noqa: BLE001
            raise WenshuError(
                "浏览器后端需要 playwright。请 `pip install playwright` 并"
                " `playwright install chromium`，或改用默认 requests 后端。"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._ctx = self._browser.new_context(
            locale="zh-CN", user_agent=C.DEFAULT_USER_AGENT,
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._page = self._ctx.new_page()

    # ------------------------------------------------------------------ #
    # 会话快照（storage_state 持久化）
    # ------------------------------------------------------------------ #
    def save_state(self, path: Optional[str] = None) -> Optional[str]:
        """把当前浏览器上下文的 Cookie/localStorage 快照落盘。

        这是「免重复点验证码」的关键：走完一次 OAuth（人工过点选验证码）后把
        会话存下来，下次进程启动直接 restore，不必再打扰用户。

        :return: 成功返回落盘路径；失败返回 None（不抛异常，持久化失败不应
                 影响主流程）。
        """
        target = path or self.session_state_path
        if self._ctx is None or not target:
            return None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            self._ctx.storage_state(path=target)
            self._log(f"会话快照已保存: {target}")
            return target
        except Exception as e:  # noqa: BLE001
            self._log(f"保存会话快照失败（忽略）: {type(e).__name__}: {e}")
            return None

    @staticmethod
    def state_has_live_session(path: str) -> bool:
        """离线检查快照里是否还有未过期的 SESSION Cookie（不发任何网络请求）。

        Playwright 的 storage_state 里，持久化 Cookie 带 ``expires``（Unix 秒）；
        会话级 Cookie 为 ``-1``（关掉浏览器即失效，无法通过快照复活）。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            return False
        now = time.time()
        for ck in (data.get("cookies") or []):
            if ck.get("name") != "SESSION":
                continue
            exp = ck.get("expires", -1)
            try:
                exp = float(exp)
            except (TypeError, ValueError):
                exp = -1.0
            # -1 / 0 = 会话 Cookie，快照里复活不了；过期同样不可用
            if exp <= 0:
                return False
            return exp > now
        return False

    def restore(self, path: Optional[str] = None) -> bool:
        """从快照恢复浏览器上下文，并导航到搜索应用页。

        只负责「把上下文立起来」，**不做探活**——快照能否真正命中数据要由
        调用方发一次轻量请求验证（见 WenshuClient.try_restore_session）。

        :return: 是否成功建立上下文（失败时内部已清理，调用方可安全回退 OAuth）。
        """
        target = path or self.session_state_path
        if not target or not os.path.exists(target):
            return False
        if not self.state_has_live_session(target):
            self._log("快照中的 SESSION 已过期或为会话级 Cookie，放弃恢复")
            return False
        try:
            # 与 login 的 OAuth 路径同理：若已接管着一个 playwright 实例，必须先
            # close 拆掉，否则新的 sync_playwright().start() 会撞上「Sync API
            # inside asyncio loop」。
            if self._pw is not None:
                self.close()
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:  # noqa: BLE001
                raise WenshuError(
                    "浏览器后端需要 playwright。请 `pip install playwright` 并"
                    " `playwright install chromium`。"
                ) from e
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            self._ctx = self._browser.new_context(
                locale="zh-CN", user_agent=C.DEFAULT_USER_AGENT,
                storage_state=target,
            )
            self._ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            self._ctx.set_default_timeout(8000)
            self._page = self._ctx.new_page()

            # 导航到搜索应用页：page.url 决定后续 fetch 的实际 Referer
            # （forbidden header，无法在 JS 中覆盖），必须停在应用页。
            self._page.goto(C.SEARCH_APP_URL, wait_until="load",
                            timeout=self.timeout * 1000)
            self._page.wait_for_timeout(3000)
            self._sync_app_path()
            self._trusted = True
            self.session_source = "restored"
            self._log(f"已从快照恢复会话: {target}")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"快照恢复失败，回退 OAuth: {type(e).__name__}: {e}")
            self.close()
            return False

    def _sync_app_path(self) -> None:
        """按当前 page.url 同步 C.SEARCH_APP_URL / SEARCH_REFERER。

        Referer 恒等于 page.url（forbidden header），所以 SEARCH_APP_URL 必须与
        真实页面一致，否则构造出的 Referer 与实际不符。
        """
        import re as _re
        cur = self._page.url or ""
        m = _re.search(r"/website/wenshu/([A-Za-z0-9]+)/index\.html", cur)
        if m:
            app = m.group(1)
            if app != C.SEARCH_APP_PATH:
                C.SEARCH_APP_URL = f"{C.BASE_URL}/website/wenshu/{app}/index.html"
                C.SEARCH_REFERER = f"{C.SEARCH_APP_URL}?pageId={C.PAGE_ID}"

    @staticmethod
    def _log(msg: str) -> None:
        """诊断日志：仅 WENSHU_DEBUG=1 时输出（不污染 MCP 的 stdout JSON-RPC）。"""
        if os.getenv("WENSHU_DEBUG"):
            import sys
            print(f"[backend][{time.time():.0f}] {msg}", file=sys.stderr, flush=True)

    def login(self, username=None, password=None, cookies=None,
              max_attempts: int = 6, wait_after: int = 4,
              solve_mode: str = "auto", human_timeout: int = 300):
        """在浏览器上下文内完成登录（OAuth 或注入 Cookie 后导航建立 WAF 信任）。

        返回时会话已在本浏览器上下文内「受信任」：search/download 经本后端发出
        的请求不会被 wzws 软拦截。

        :param username/password: OAuth 凭据；为 None 时由 auth_oauth 从 .env 读取。
        :param cookies: 已登录 SESSION（dict 或 Playwright Cookie 列表）。注入到
                        浏览器上下文后导航首页，使该上下文成为 WAF 信任的会话。
                        （这是注入用户自己浏览器导出的 SESSION 时的可靠路径。）
        :param max_attempts: OAuth 重试次数。
        :param wait_after:  rivate Cookie 后导航首页的等待秒数（让 WAF 握手完成）。
        :param solve_mode: 验证码求解策略（"auto"/"human"/"auto_then_human"）。
                           人工模式会弹出浏览器窗口，由用户手动完成点选验证码。
        :param human_timeout: 人工模式等待用户完成验证码的最长秒数。
        """
        # 注入 Cookie 路径：需要一个我们自己启动的浏览器上下文，把 Cookie 写进去，
        # 再导航首页以建立会话信任。
        if cookies is not None:
            self._ensure_browser()
            self._add_cookies(cookies)
            # ★必须导航到「应用页」而非首页：Referer 恒等于 page.url（forbidden
            # header），停在首页会让网关认为请求不来自搜索应用而拒（code=9）。
            self._page.goto(C.SEARCH_APP_URL, wait_until="load",
                            timeout=self.timeout * 1000)
            self._page.wait_for_timeout(wait_after * 1000)
            self._sync_app_path()
            self._trusted = True
            self.session_source = "inject"
            self.save_state()
            return self

        # OAuth 路径：oauth_login(keep_open=True) 会自己启动并返回浏览器/上下文/页面，
        # 由本后端直接「接管」。★这里绝不能先调 _ensure_browser()：那会先启一个
        # sync_playwright 实例（其事件循环处于 running 态），随后 oauth_login 内部再次
        # sync_playwright().start() 就会命中「Sync API inside asyncio loop」而崩溃，
        # 且先启的浏览器会被覆盖泄漏。
        #
        # ★重登场景：若本后端此前已接管过一个常驻 playwright（self._pw 非空），必须先
        # close() 拆掉它，否则同样会与新 oauth_login 的 sync_playwright 撞运行中的事件
        # 循环。close() 后再走一次全新 OAuth。
        if self._pw is not None:
            self.close()

        from .auth_oauth import oauth_login

        live = oauth_login(
            username=username, password=password,
            headless=self.headless, max_attempts=max_attempts,
            captcha_solver=self.captcha_solver, keep_open=True,
            solve_mode=solve_mode, human_timeout=human_timeout,
        )
        # live: {cookies, pw, browser, context, page}
        self._pw = live["pw"]
        self._browser = live["browser"]
        self._ctx = live["context"]
        self._page = live["page"]
        # ★关键（2026-08-29 决定性结论，改写此前多条误判）：
        #   1) 浏览器内 fetch 的 `Referer` 是 **forbidden header**，JS 无法覆盖，
        #      实际 Referer 永远等于当前 `page.url`。故搜索能否成功，只取决于
        #      「登录后 page.url 是否停在搜索应用页」（任意有效的
        #      /website/wenshu/<app>/index.html）。diag_real_request.py 实测：回跳停在
        #      应用页时搜索命中 1406 万；回跳到非应用页（如首页）时 Referer 不对 →
        #      网关 code=9（JSONNull）。
        #   2) 「wzws_reurl 是信任标记」已被证伪：诊断脚本无 wzws_reurl 仍成功。
        #   3) 「goto 清空信任态」也被证伪（OAuth 回跳本身就是浏览器导航到应用页且成功）。
        #   4) OAuth 回跳目标**不固定**（有时应用页、有时首页）。故此处主动「确保 page.url
        #      停在搜索应用页」：回跳已是应用页则保持；回跳到非应用页则 goto 到已知
        #      有效应用页入口。
        import re as _re
        cur = (self._page.url or "")
        if _re.search(r"/website/wenshu/([A-Za-z0-9]+)/index\.html", cur):
            # 回跳已是搜索应用页：保持不动，仅同步真实 app path（与 page.url 一致）。
            self._sync_app_path()
        else:
            # 回跳不是应用页（如首页）：导航到已知有效应用页入口，让 Referer 正确。
            _app_url = C.LOGIN_PAGE_URL.split("?")[0]
            try:
                self._page.goto(_app_url, wait_until="load",
                                timeout=self.timeout * 1000)
                self._page.wait_for_timeout(4000)  # 应用页 JS 初始化、SESSION 绑定
                self._sync_app_path()   # 跟随 goto 后的真实 page.url（含重定向）
            except Exception:  # noqa: BLE001
                pass
        # wzws_reurl 已被证伪为「信任标记」（多次成功检索时根本不存在），故不再
        # 白等 20s 轮询。登录后停在搜索应用页即视为上下文就绪。
        self._trusted = True
        self.session_source = "oauth"
        # ★落盘会话快照：后续进程启动可直接 restore，跳过人工点选验证码。
        self.save_state()
        return self

    def _add_cookies(self, cookies) -> None:
        """把 Cookie 注入浏览器上下文（仅保留 wenshu 域）。"""
        if isinstance(cookies, dict):
            items = list(cookies.items())
        else:
            items = [
                (c.get("name"), c.get("value"))
                for c in cookies
                if "wenshu.court.gov.cn" in (c.get("domain") or "")
            ]
        ck = [
            {"name": n, "value": v, "domain": ".wenshu.court.gov.cn", "path": "/"}
            for n, v in items if n
        ]
        if ck:
            self._ctx.add_cookies(ck)

    # ------------------------------------------------------------------ #
    # 网关请求
    # ------------------------------------------------------------------ #
    def fetch_gateway(self, form: dict, referer: str = None) -> str:
        """在浏览器上下文内 POST 网关，返回响应文本（JSON 信封）。

        浏览器内 fetch 自动携带本上下文的 Cookie 与浏览器指纹，WAF 认可，
        从而绕过跨上下文重放的软拦截。返回文本形如
        '{"code":1,"secretKey":"...","result":"..."}'，由 Python 侧 3DES 解密。

        ★Referer 处理（关键）：优先采用调用方传入的 Referer。调用方
        （``client._search_raw`` / ``_get_document_raw``）已按站点真实请求构造好
        带有 ``pageId``（搜索）或 ``docId``（详情）的 Referer；实测证明「Referer 的
        pageId 与表单 pageId 一致」时网关才放行（见 research/e2e_browser_download.py）。
        若改用 ``page.url``（OAuth 回跳后多为应用页基址、不带 pageId 查询串），网关会以
        ``code=9`` 拒绝（JSONNull cast）。故这里**优先用调用方 referer**，仅在取不到时
        才退回 ``page.url``。此外 fetch 请求头必须是 ISO-8859-1，故对 Referer
        兜底 URL 编码，避免中文导致 fetch 抛错。
        """
        if self._page is None:
            raise WenshuError("浏览器后端未启动，请先 login()。")

        real_referer = (referer or self._page.url or C.BASE_URL)
        # 保证 Referer 为 ISO-8859-1 安全（fetch headers 限制）：非 Latin-1 字符编码
        try:
            real_referer.encode("latin-1")
        except UnicodeEncodeError:
            from urllib.parse import quote
            real_referer = quote(real_referer, safe=":/?=&%")

        # ★健壮性：浏览器内 fetch 默认没有超时，一旦网关请求挂起（登录后页面仍在
        # 重定向、或网关偶发无响应），page.evaluate 会无限阻塞主线程。用 AbortController
        # 强制 ~30s 兜底，把「卡死」变成可捕获的 'ERR:timeout'（NetworkError），
        # 而不是让整个进程永远停在 search/download 阶段。
        fetch_timeout_ms = int(max(30, getattr(self, "timeout", 30)) * 1000)
        js = r"""
        ({form, referer, timeoutMs}) => new Promise((resolve) => {
          var body = Object.keys(form).map(k =>
            encodeURIComponent(k) + '=' + encodeURIComponent(form[k])).join('&');
          var ctrl = new AbortController();
          var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs);
          fetch('/website/parse/rest.q4w', {
            method: 'POST',
            credentials: 'include',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest',
              'Referer': referer
            },
            body: body,
            signal: ctrl.signal
          }).then(r => r.text()).then(function (t) {
            clearTimeout(timer);
            resolve(t);
          }).catch(function (e) {
            clearTimeout(timer);
            resolve('ERR:' + (e && e.name === 'AbortError' ? 'timeout' : String(e)));
          });
        });
        """
        return self._page.evaluate(
            js, {"form": form, "referer": real_referer, "timeoutMs": fetch_timeout_ms}
        )

    def ping(self) -> bool:
        """在浏览器上下文内轻量探活（GET 首页），best-effort 拉伸 SESSION TTL。

        用于 AgentSession 的保活心跳：证明本浏览器上下文仍「受信任」且会话未失效。
        返回 True 表示首页可达（200）。仅在已登录上下文内有意义。
        """
        if self._page is None:
            return False
        # ★Playwright Sync 的 greenlet 绑定到创建线程（主线程）。心跳在守护线程中调用，
        # 跨线程 evaluate 会抛 greenlet.error 且无效。故非主线程时直接返回 True（视作
        # 存活），避免崩溃与 fiber 损坏；真实探活留给主线程场景，真正的保活过期兜底由
        # 业务请求的 _with_relogin 负责。
        import threading
        if threading.current_thread() is not threading.main_thread():
            return True
        # 与 fetch_gateway 一致的兜底：ping 也带 AbortController，避免心跳线程被
        # 一个永远不返回的 fetch 永久阻塞（否则心跳线程卡死、后续探活全部失效）。
        js = r"""
        () => new Promise((resolve) => {
          var ctrl = new AbortController();
          var timer = setTimeout(function () { ctrl.abort(); }, 15000);
          fetch('/', {method: 'GET', credentials: 'include', signal: ctrl.signal})
            .then(function (r) {
              clearTimeout(timer);
              resolve(r.status === 200 ? 'OK' : ('HTTP:' + r.status));
            })
            .catch(function (e) {
              clearTimeout(timer);
              resolve('ERR:' + (e && e.name === 'AbortError' ? 'timeout' : String(e)));
            });
        });
        """
        try:
            res = self._page.evaluate(js)
            return isinstance(res, str) and res.startswith("OK")
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # 信任建立 / 重暖
    # ------------------------------------------------------------------ #
    def re_warm(self, timeout: int = 3) -> bool:
        """轻量重暖（只读，不导航、不弹验证码、不轮询 wzws_reurl）。

        诊断已证伪 wzws_reurl 是「信任标记」——多次成功检索时它根本不存在，故不再
        浪费 20s 轮询一个永不出现的 cookie。这里仅做极短 settle，给网关一点时间；
        真正的过期兜底由 AgentSession._with_relogin 的整轮重登负责。
        """
        if self._page is None:
            return False
        try:
            self._page.wait_for_timeout(max(0, int(timeout)) * 1000)
        except Exception:  # noqa: BLE001
            pass
        return True

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """关闭浏览器与 Playwright，释放资源。

        ★必须在创建该 Playwright 的同一线程（主线程）调用：Playwright Sync API 绑定
        greenlet 到其创建线程，跨线程调用会抛 "Cannot switch to a different thread"。
        因此这里直接同步关闭，不另起线程。close() 设计上用于「重登前拆旧实例」与
        进程退出，调用频率低，无需超时兜底。
        """
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._browser = self._ctx = self._page = None
