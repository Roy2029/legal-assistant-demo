"""OAuth 登录辅助（account.court.gov.cn）。

裁判文书网的登录已是 OAuth 流程（client_id=zgcpwsw），旧的
`crud/rest.q4w` + `AppUserDTO@login` 直登通道已废弃。本模块用 Playwright 驱动
真实浏览器完成 OAuth 登录，返回 wenshu.court.gov.cn 的 Cookie 字典
（含 SESSION），供 WenshuClient 注入 requests.Session 复用。

仅在以下情况由 WenshuClient.login() 调用：
  - 用户未传入 cookies= ；且
  - 本机已安装 playwright + ddddocr。

否则 WenshuClient.login() 会提示用户改用 cookies= 注入浏览器导出的会话。
"""
from __future__ import annotations

import base64
import os
import sys
import time
import threading
from typing import Callable, Optional

from dotenv import load_dotenv


# 验证码求解策略
SOLVE_AUTO = "auto"            # ddddocr 自动识别「输入字符」型验证码
SOLVE_HUMAN = "human"          # 弹出浏览器窗口，由用户在页面内手动完成「点选文字」验证码
SOLVE_AUTO_HUMAN = "auto_then_human"  # 先自动识别，失败后再转人工


def _dbg(msg: str) -> None:
    """诊断日志：仅当 WENSHU_DEBUG=1 时输出到 stderr（不污染 MCP 的 stdout JSON-RPC）。"""
    if os.getenv("WENSHU_DEBUG"):
        print(f"[oauth][{time.time():.0f}] {msg}", file=sys.stderr, flush=True)


def _safe_stop(pw, browser) -> None:
    """后台线程关闭浏览器，避免 pw.stop() 偶发 hang（chromium driver 卡住）阻塞主流程。"""
    def _kill():
        try:
            if browser is not None:
                browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass
    t = threading.Thread(target=_kill, daemon=True)
    t.start()
    t.join(timeout=12)

# wenshu_api/.env
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
# 兼容从仓库根运行
_ROOT_ENV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wenshu_api", ".env"
)


def _load_creds(username: Optional[str], password: Optional[str]):
    for cand in (_ENV_PATH, _ROOT_ENV, os.path.join(os.getcwd(), ".env")):
        if os.path.exists(cand):
            load_dotenv(cand)
    username = username or os.getenv("WENSHU_USER_NAME")
    password = password or os.getenv("WENSHU_PASSWORD")
    return (username or "").strip(), (password or "").strip()


def oauth_login(
    username: Optional[str] = None,
    password: Optional[str] = None,
    headless: bool = True,
    max_attempts: int = 6,
    captcha_solver: Optional[Callable[[bytes, str], str]] = None,
    keep_open: bool = False,
    solve_mode: str = "auto",
    human_timeout: int = 300,
) -> dict:
    """用 Playwright 走 OAuth 登录，返回 Cookie 字典（name -> value）。

    :param username/password: 凭据；为 None 时从 .env 读取。
    :param headless: 是否无头。
    :param max_attempts: 登录重试次数（验证码识别失败会自动刷新重试）。
    :param captcha_solver: 可选自定义验证码求解回调 (image_bytes, url) -> str；
                           默认用 ddddocr 离线识别「输入字符」型验证码。
    :param keep_open: 返回浏览器/上下文/页面活对象（浏览器后端常驻用）。
    :param solve_mode: 验证码求解策略（环境变量 ``WENSHU_SOLVE_MODE`` 可覆盖）：
        - ``auto``（默认）：ddddocr 自动识别「输入字符」型验证码；
        - ``human``：弹出浏览器窗口，由用户在页面内手动完成「点选文字」验证码，
                     程序轮询登录结果（适合新版点选验证码）；
        - ``auto_then_human``：先自动识别，失败后再转人工。
    :param human_timeout: 人工模式下等待用户完成验证码的最长秒数。

    调试逃生口：设 ``WENSHU_HEADED=1`` 可强制显示浏览器窗口（对本函数所有调用路径生效）。
    """
    # 解析求解策略（环境变量优先覆盖参数）
    solve_mode = (os.getenv("WENSHU_SOLVE_MODE") or solve_mode).strip().lower()
    if os.getenv("WENSHU_HEADED", "").strip().lower() in ("1", "true", "yes"):
        headless = False
    # 人工介入必须可见窗口：让用户能看到并操作验证码
    if solve_mode in (SOLVE_HUMAN, SOLVE_AUTO_HUMAN):
        headless = False

    try:
        from playwright.sync_api import sync_playwright
        import ddddocr
    except ImportError as e:
        raise RuntimeError(
            "OAuth 登录需要 playwright 与 ddddocr。请 `pip install playwright ddddocr` "
            "并 `playwright install chromium`，或改用 WenshuClient.login(cookies=...) "
            "注入浏览器导出的已登录会话。"
        ) from e

    username, password = _load_creds(username, password)
    if not username or not password:
        raise RuntimeError(
            "缺少登录凭据：请在 wenshu_api/.env 设置 WENSHU_USER_NAME / WENSHU_PASSWORD，"
            "或显式传入 username/password，或改用 cookies= 注入已登录会话。"
        )

    ocr = ddddocr.DdddOcr(show_ad=False)

    def solve(image_bytes: bytes, _url: str) -> str:
        if captcha_solver is not None:
            return captcha_solver(image_bytes, _url)
        return ocr.classification(image_bytes)

    def get_oauth_url(page, timeout=20000):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for f in page.frames:
                if "account.court.gov.cn/oauth/authorize" in f.url:
                    return f.url
            try:
                src = page.get_attribute("#contentIframe", "src")
                if src and "account.court.gov.cn/oauth/authorize" in src:
                    return src
            except Exception:
                pass
            page.wait_for_timeout(1000)
        return None

    def find_captcha_datauri(page):
        for el in page.query_selector_all("img"):
            s = el.get_attribute("src") or ""
            if s.startswith("data:image"):
                return s
        return None

    def find_captcha_img(page):
        """定位验证码图片元素（用于点击刷新 + 读取）。

        优先级：1) 已是 data: URI 内联（最常见）；2) src/id/alt 含验证码关键词；
        3) 兜底取第一个 img。避免「盲点第一个 img（可能是 logo）」导致刷新点击无效。
        """
        for el in page.query_selector_all("img"):
            s = el.get_attribute("src") or ""
            if s.startswith("data:image"):
                return el
        for kw in ("captcha", "code", "valid", "yzm", "verify", "rand"):
            el = page.query_selector(
                f"img[src*='{kw}'], img[id*='{kw}'], img[alt*='{kw}']"
            )
            if el:
                return el
        return page.query_selector("img")

    def do_login(page):
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", password)
        # 刷新验证码（best-effort，短超时，绝不卡死）：点中验证码图换一张
        cap = find_captcha_img(page)
        if cap is not None:
            try:
                cap.click(timeout=4000)
                page.wait_for_timeout(700)
                _dbg("captcha img clicked (refresh)")
            except Exception:  # noqa: BLE001
                _dbg("captcha img click skipped")
        uri = find_captcha_datauri(page)
        if not uri:
            return False, "无验证码图片"
        _dbg("captcha read")
        code = solve(base64.b64decode(uri.split(",", 1)[1]), "account.captcha")
        page.fill("input[name=captcha]", code)
        page.wait_for_timeout(250)
        # 提交：多候选选择器，best-effort（任一命中即点，失败回退回车）
        submitted = False
        try:
            btn = page.query_selector(
                "button[type=submit], input[type=submit], "
                "button:has-text('登录'), button:has-text('立即登录'), "
                "button:has-text('登 录'), input[value*='登录'], "
                "#loginBtn, .login-btn, .btn-login"
            )
            if btn is not None:
                btn.click(timeout=6000)
                submitted = True
                _dbg("submit clicked")
        except Exception:  # noqa: BLE001
            submitted = False
            _dbg("submit click failed")
            if not submitted:
                try:
                    page.press("input[name=captcha]", "Enter", timeout=3000)
                except Exception:  # noqa: BLE001
                    pass
            return True, code

    def submit_login_form(page) -> bool:
        """人工模式专用：填表并提交，不做 OCR。返回是否成功触发提交。"""
        try:
            page.fill("input[name=username]", username)
            page.fill("input[name=password]", password)
            btn = page.query_selector(
                "button[type=submit], input[type=submit], "
                "button:has-text('登录'), button:has-text('立即登录'), "
                "button:has-text('登 录'), input[value*='登录'], "
                "#loginBtn, .login-btn, .btn-login"
            )
            if btn is not None:
                btn.click(timeout=6000)
                return True
            # 兜底：在密码框回车提交
            page.press("input[name=password]", "Enter", timeout=3000)
            return True
        except Exception as e:  # noqa: BLE001
            _dbg(f"submit_login_form failed: {e}")
            return False

    def _eval_js(page, js, default=None):
        """安全执行页面 JS（best-effort），异常时返回默认值，绝不卡死。"""
        try:
            return page.evaluate(js)
        except Exception:  # noqa: BLE001
            return default

    # 风险管控/二次验证覆盖层检测：文书网在登录后可能对新 IP / 自动化特征弹出
    # 「数字验证码（附带 IP 信息）」等二次验证页。该页停在 wenshu 域，仅按 URL
    # 判断会被误判为「登录成功」，导致 fetch_gateway 因会话未完全建立而 code=9。
    # 故需显式检测并等待其被用户手动完成。该验证码常以 iframe 承载（行为验证码）。
    _BLOCKING_JS = r"""
    () => {
      function visible(el){
        if(!el) return false;
        var r=el.getBoundingClientRect(); var s=getComputedStyle(el);
        return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
      }
      var nodes = Array.prototype.slice.call(document.querySelectorAll('input,iframe'));
      for (var i=0;i<nodes.length;i++){
        var el=nodes[i];
        if(el.tagName==='IFRAME' && visible(el)){ return true; }   // 行为验证码 iframe
        if(el.tagName==='INPUT'){
          if(!visible(el)) continue;
          var sig=((el.id||'')+' '+(el.placeholder||'')+' '+(el.name||'')+' '+(el.className||'')).toLowerCase();
          if(/code|captcha|verify|valid|rand|yzm|auth|check|secure|risk/.test(sig)){
            var txt=(document.body.innerText||'');
            if(/验证|安全|ip|请输入|风险|滑动|点选|身份/.test(txt)) return true;
          }
        }
      }
      var body=(document.body.innerText||'');
      if(/请输入.*(验证|校验)码|安全验证|风险控制|验证您的身份|操作过于频繁/.test(body)) return true;
      return false;
    }
    """

    _LOGGEDIN_JS = r"""
    () => {
      var body=(document.body.innerText||'');
      if(/退出|个人中心|我的文书|用户中心|您好|欢迎您|注销/.test(body)) return true;
      var loginBtns=Array.prototype.slice.call(document.querySelectorAll('a,button')).filter(function(e){
        return /(登录|注册|点此登录|立即登录)/.test(e.innerText||'');
      });
      // 没有明确的登录入口，且页面含账户相关字样，视为已登录
      if(loginBtns.length===0 && /(账户|账号|会员|我的)/.test(body)) return true;
      return false;
    }
    """

    def wait_human_login(page, timeout: int) -> bool:
        """人工模式：等待用户「完整」完成登录（含可能弹出的二次验证 / 数字验证码）。

        判定比单纯看 URL 更稳，避免把「停在 wenshu 域的二次验证页」误判为已登录：
          1) 必须回到 wenshu.court.gov.cn 域（OAuth 回跳完成）；
          2) 当前页面不得有未完成的「风险管控 / 二次验证」覆盖层（文书网常弹出
             附带 IP 信息的数字验证码，单看 URL 会误判为已登录）；
          3) 需确认已真正登录（账户标记出现），或已在首页且无阻塞达宽限期（6s）。
        任一时刻若检测到阻塞层，提示用户在浏览器中完成并持续等待，绝不提前退出。
        """
        deadline = time.time() + timeout
        on_wenshu_since = None
        last_hint = 0.0
        while time.time() < deadline:
            url = page.url or ""
            on_wenshu = ("wenshu.court.gov.cn" in url) and ("account.court.gov.cn" not in url)
            if on_wenshu:
                if on_wenshu_since is None:
                    on_wenshu_since = time.time()
                blocking = _eval_js(page, _BLOCKING_JS, default=False)
                if blocking:
                    now = time.time()
                    if now - last_hint > 10:
                        print(
                            "[wenshu] 检测到二次验证 / 数字验证码，请在浏览器中手动完成"
                            "（含 IP 信息的验证码页），完成后将自动继续。",
                            file=sys.stderr, flush=True,
                        )
                        last_hint = now
                else:
                    logged_in = _eval_js(page, _LOGGEDIN_JS, default=False)
                    if logged_in or (time.time() - on_wenshu_since > 6):
                        return True
            else:
                on_wenshu_since = None
            try:
                page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001
                pass
        return False

    pw = sync_playwright().start()
    _dbg("playwright started")
    browser = None
    try:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        _dbg("browser launched")
        ctx = browser.new_context(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        # 所有页面动作（fill/click/press/goto 的 load 事件）默认 8s 超时，
        # 避免元素不可交互时 Playwright 默默等满 30s × 多次重试 → 整体卡死数分钟。
        ctx.set_default_timeout(8000)
        page = ctx.new_page()
        _dbg("page created")
        page.goto(
            "https://wenshu.court.gov.cn/website/wenshu/181010CARHS5BS3C/index.html",
            wait_until="load",
            timeout=30000,
        )
        _dbg("index.html loaded")
        page.wait_for_timeout(3000)
        oauth_url = get_oauth_url(page)
        _dbg(f"oauth_url={'found' if oauth_url else 'NONE'}")
        if not oauth_url:
            raise RuntimeError("无法定位 OAuth 授权地址（account.court.gov.cn/oauth/authorize）")
        page.goto(oauth_url, wait_until="load", timeout=30000)
        _dbg("oauth page loaded")
        page.wait_for_timeout(4000)

        logged = False
        last_code = ""

        if solve_mode == SOLVE_HUMAN:
            # 人工介入：提交表单后由用户在浏览器内手动完成「点选文字」验证码
            if not submit_login_form(page):
                raise RuntimeError("无法提交登录表单（未找到登录按钮或表单不可用）")
            print(
                "[wenshu] 已打开登录窗口：请在浏览器中手动完成点选验证码，"
                "登录成功（自动跳回 wenshu.court.gov.cn）后将自动继续。",
                file=sys.stderr, flush=True,
            )
            logged = wait_human_login(page, human_timeout)
            if not logged:
                raise RuntimeError(
                    f"人工登录超时（{human_timeout}s 内未完成）。请确认凭据正确，"
                    "或在浏览器窗口内重试；也可先用浏览器登录后导出 SESSION 再用 cookies= 注入。"
                )
        else:
            # auto / auto_then_human：ddddocr 自动识别「输入字符」型验证码
            login_deadline = time.time() + 120
            for attempt in range(max_attempts):
                if time.time() > login_deadline:
                    _dbg("watchdog: login_deadline exceeded, breaking")
                    break
                _dbg(f"attempt {attempt + 1}/{max_attempts}")
                ok, last_code = do_login(page)
                if not ok:
                    raise RuntimeError("OAuth 登录页未出现验证码")
                page.wait_for_timeout(5000)
                if "wenshu.court.gov.cn" in page.url and "account.court.gov.cn" not in page.url:
                    logged = True
                    break
                # 失败后等待验证码刷新
                page.wait_for_timeout(1500)
            if not logged and solve_mode == SOLVE_AUTO_HUMAN:
                print(
                    "[wenshu] 自动识别失败，转人工：请在浏览器中手动完成点选验证码。",
                    file=sys.stderr, flush=True,
                )
                if submit_login_form(page):
                    logged = wait_human_login(page, human_timeout)
                if not logged:
                    raise RuntimeError(
                        "自动识别失败且人工登录超时，请检查凭据后重试。"
                    )

        if not logged:
            # best-effort  存调试截图，便于人工核对登录页形态（凭据绝不入图）
            shot_msg = ""
            try:
                shot = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "oauth_login_failed.png"
                )
                page.screenshot(path=shot, timeout=5000)
                shot_msg = f"（已存调试截图：{shot}）"
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"OAuth 登录失败（已重试 {max_attempts} 次，最后验证码识别={last_code!r}）。{shot_msg}"
                "请确认 .env 凭据正确；如验证码频繁识别错误，可手动从浏览器导出 SESSION 后"
                "用 WenshuClient.login(cookies=...) 注入，或加 --headed 人工辅助过验证码。"
            )

        cookies = {c["name"]: c["value"] for c in ctx.cookies()
                   if "wenshu.court.gov.cn" in (c.get("domain") or "")}
        if keep_open:
            # 浏览器后端模式：保持浏览器上下文常驻，返回活对象给调用方管理。
            return {"cookies": cookies, "pw": pw, "browser": browser,
                    "context": ctx, "page": page}
        _safe_stop(pw, browser)
        return cookies
    except Exception:
        _safe_stop(pw, browser)
        raise
