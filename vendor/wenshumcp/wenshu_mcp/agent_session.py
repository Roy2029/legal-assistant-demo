"""AgentSession：把「反爬摩擦」包裹起来的会话编排层。

这是「0 感知」体验的核心：上层 agent 只需调用 search / get_document / download，
本层负责：

1. **单例常驻**：进程内唯一 WenshuClient（浏览器后端默认），不每次开关浏览器。
2. **透明自动重登**：业务调用抛 SessionExpiredError 时，自动重跑 OAuth 后重试一次，
   对 agent 完全透明。
3. **保活心跳（best-effort）**：空闲时低频探活拉伸 SESSION TTL；但明确——
   空闲超过 TTL 必然过期，届时由自动重登兜底，不承诺「永不重登」。
4. **会话健康度**：session_status() 暴露登录态 / 年龄 / 后端 / 最近成功时间。

本文件保持**明文**（编排逻辑，无敏感算法）；真正的协议 / 解密逻辑在 wenshu_api 内核。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("wenshu_mcp.agent_session")


class AgentSession:
    def __init__(
        self,
        backend: str = "browser",
        max_qps: float = 1.0,
        max_retries: int = 3,
        timeout: int = 15,
        heartbeat_interval: int = 300,
        log_level: int = 20,
        cooldown_sec: float = 900.0,
        auto_restore: bool = True,
    ):
        if backend not in ("requests", "browser"):
            raise ValueError(f"backend 仅支持 requests / browser，收到 {backend!r}")
        self.backend = backend
        self.heartbeat_interval = heartbeat_interval
        self.auto_restore = auto_restore
        self._client_kwargs = dict(
            max_qps=max_qps, max_retries=max_retries,
            timeout=timeout, log_level=log_level, backend=backend,
        )
        self._client = None
        self._login_time: Optional[float] = None
        self._last_ok: Optional[float] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = True
        # 记住登录时用的求解策略，供自动重登（_with_relogin）复用同一模式。
        # ★默认 human：站点登录验证码已改型为「点选文字」（tianai WORD_IMAGE_CLICK），
        #   离线 OCR 实测无法可靠识别（2026-08-28 结论），auto 模式基本必失败。
        #   默认 human 意味着需要登录时会弹窗让人点，而不是静默重试到超时。
        #   大多数情况下走不到这一步——会话快照复用会先命中，从而跳过验证码。
        try:
            from wenshu_api.auth_oauth import SOLVE_HUMAN as _DEFAULT_MODE
        except Exception:  # noqa: BLE001
            _DEFAULT_MODE = "human"
        self._login_solve_mode: str = _DEFAULT_MODE
        self._login_human_timeout: int = 300
        # 会话来源：restored（复用快照）/ oauth（刚过验证码）/ None（未登录）
        self._session_source: Optional[str] = None
        # 跨进程限频冷却窗口（code=9 是 IP 绑定的服务端限频，重登换不掉）
        from wenshu_api.rate_guard import get_guard
        self._guard = get_guard(cooldown_sec=cooldown_sec)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _get_client(self):
        if self._client is None:
            from wenshu_api import WenshuClient
            self._client = WenshuClient(**self._client_kwargs)
        return self._client

    def _login(self, client, solve_mode: str = "auto",
               human_timeout: int = 300) -> None:
        client.login(solve_mode=solve_mode, human_timeout=human_timeout)
        self._login_time = time.time()
        self._last_ok = time.time()

    def _mark_ok(self) -> None:
        """一次成功请求后：刷新活跃时间并清除限频冷却。"""
        self._last_ok = time.time()
        self._guard.clear()

    def _session_ready(self, client) -> bool:
        """当前是否已持有可用的浏览器上下文（不必再登录）。"""
        if not getattr(client, "logged_in", False):
            return False
        if self.backend == "browser":
            bb = getattr(client, "_browser_backend", None)
            return bb is not None and getattr(bb, "_page", None) is not None
        return True

    def _ensure_session(self):
        """确保处于已登录状态，优先级：复用快照 > 重新登录。

        这是「0 感知」的关键一环：有可用快照时直接复用，用户不必再点一次
        点选验证码；只有快照不存在 / 已过期 / 探活失败时才走完整 OAuth。

        冷却期内**不会**触发重新登录——限频绑定 IP，重登换不掉，此时弹验证码
        只是白白消耗用户一次人工操作，不如明确告知还要等多久。
        """
        client = self._get_client()
        if self._session_ready(client):
            return client

        left = self._guard.remaining()
        # 1) 快照复用（内部自带一次 page_size=1 的轻量探活）
        if self.auto_restore and self.backend == "browser" and left <= 0:
            try:
                if client.try_restore_session():
                    self._login_time = time.time()
                    self._last_ok = time.time()
                    self._session_source = "restored"
                    logger.info("[AgentSession] 已复用上次会话快照，跳过验证码")
                    return client
            except Exception as e:  # noqa: BLE001
                logger.info("[AgentSession] 快照复用不可用，转正常登录：%s", e)

        # 2) 重新登录：冷却期内直接放弃，不烧验证码
        if left > 0:
            from wenshu_api.exceptions import RateLimitError
            raise RateLimitError(
                f"服务端限频冷却中，还需等待约 {int(left)} 秒。限频绑定 IP，"
                f"重新登录（换新 SESSION）也无效，故不再打扰你点验证码。"
                f"如需立即重试可调用 reset_cooldown() 强制清除。",
                retry_after=int(left),
            )

        self._login(
            client,
            solve_mode=self._login_solve_mode,
            human_timeout=self._login_human_timeout,
        )
        self._session_source = "oauth"
        return client

    def _with_relogin(self, fn, same_session_retries: int = 2):
        """执行业务函数；遇 SessionExpiredError（code=9）分级自愈。

        code=9 两种成因，处理策略不同（2026-08-30 实测定性）：
          - **服务端限频/反爬**：同一账号/IP 短期内反复登录+检索被打标，SESSION 仍在，
            重登换不掉 IP 也救不回 → **不应烧验证码**，仅同会话退避重试，仍失败则明确报错。
          - **会话真过期**：SESSION 缺失/被服务端清除 → 整轮重登（可能需人工过验证码）兜底。

        自愈顺序：
          0) 冷却窗口内 → 直接快速失败（连试都不试，避免加剧限频）；
          1) 确保已登录（优先复用会话快照，跳过验证码）后执行业务；
          2) code=9 且 SESSION 仍在 → 同会话退避重试 N 次（覆盖瞬时/限频）；
          3) 仍失败且 SESSION 缺失 → 整轮重登后重试一次；
          4) 仍失败且 SESSION 仍在 → 判定为限频，记入冷却窗口并抛出清晰错误。
        """
        from wenshu_api.exceptions import SessionExpiredError

        # 步骤 0：冷却窗口快速失败。既然限频是 IP 绑定的，冷却期内任何请求都
        # 大概率仍被拒，与其徒劳重试（还会延长服务端窗口），不如立刻告知用户。
        left = self._guard.remaining()
        if left > 0:
            from wenshu_api.exceptions import RateLimitError
            raise RateLimitError(
                f"服务端限频冷却中，还需等待约 {int(left)} 秒再试。"
                f"（冷却窗口跨进程共享，避免反复重试延长封禁）",
                retry_after=int(left),
            )

        client = self._ensure_session()

        def _has_session() -> bool:
            try:
                bb = client._browser_backend
                if bb is None or getattr(bb, "_ctx", None) is None:
                    return False
                return any(c.get("name") == "SESSION" for c in bb._ctx.cookies())
            except Exception:  # noqa: BLE001
                return False

        last_exc = None
        # 步骤 1 + 2：直接执行；失败（SESSION 仍在时）同会话退避重试，不重登不烧验证码
        for attempt in range(1 + same_session_retries):
            try:
                result = fn(client)
                self._mark_ok()      # 成功即清除限频冷却
                return result
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, SessionExpiredError):
                    raise
                last_exc = e
                if attempt < same_session_retries:
                    backoff = 5 * (attempt + 1)
                    logger.warning(
                        "[AgentSession] code=9 可能为瞬时/限频，同会话退避 %ss 后重试 "
                        "(%d/%d，不重登不烧验证码)", backoff, attempt + 1, same_session_retries)
                    time.sleep(backoff)
                    continue
        # 同会话重试全部失败
        if _has_session():
            # SESSION 仍在 → 判定服务端限频（IP 绑定），重登换不掉 IP 也无效，且会
            # 浪费一次验证码。记入冷却窗口，后续调用直接快速失败，不再反复折腾。
            window = self._guard.mark_blocked("code=9")
            logger.error(
                "[AgentSession] code=9 但 SESSION 仍在 → 判定为服务端限频/反爬"
                "（非会话过期）。已记录 %d 秒冷却窗口，期间不再重登、不烧验证码。",
                int(window))
            if last_exc is not None:
                raise last_exc
            raise SessionExpiredError("code=9 且 SESSION 仍在，疑似服务端限频")
        # 步骤 3：SESSION 缺失 → 真过期，整轮重登兜底（烧验证码）。
        # 但冷却期内重登同样无效（限频绑 IP），先挡一道，避免白点一次验证码。
        left2 = self._guard.remaining()
        if left2 > 0:
            from wenshu_api.exceptions import RateLimitError
            raise RateLimitError(
                f"SESSION 已失效，但当前处于限频冷却期（还需 {int(left2)} 秒），"
                f"此时重登换不掉 IP 也拿不到数据，故不打扰你点验证码。请稍后再试。",
                retry_after=int(left2),
            )
        logger.warning("[AgentSession] SESSION 缺失，整轮重登后重试一次")
        try:
            self._login(
                client,
                solve_mode=self._login_solve_mode,
                human_timeout=self._login_human_timeout,
            )
            self._session_source = "oauth"
        except Exception as e3:  # noqa: BLE001
            logger.error("[AgentSession] 自动重登失败：%s", e3)
            raise e3
        result = fn(client)
        self._mark_ok()
        return result

    # ------------------------------------------------------------------ #
    # 业务接口（全部对 agent 透明）
    # ------------------------------------------------------------------ #
    def search(self, keyword=None, page=1, page_size=10, cause=None,
               court_name=None, case_type=None, trial_procedure=None, sort=None):
        return self._with_relogin(
            lambda c: c.search(
                keyword=keyword, page=page, page_size=page_size,
                cause=cause, court_name=court_name, case_type=case_type,
                trial_procedure=trial_procedure, sort=sort,
            )
        )

    def get_document(self, doc_id: str):
        return self._with_relogin(lambda c: c.get_document_content(doc_id))

    def download(self, doc_id: str, save_format: str = "text", save_path=None):
        return self._with_relogin(
            lambda c: c.download_document(
                doc_id, save_format=save_format, save_path=save_path
            )
        )

    def login_now(self, solve_mode: str = "auto",
                   human_timeout: int = 300, force: bool = False) -> dict:
        """显式触发一次登录（首配 / 手动）。

        :param solve_mode: 验证码求解策略；"human" 会弹出浏览器窗口手动完成点选验证码。
        :param human_timeout: 人工模式等待秒数。
        :param force: True 时忽略已保存的会话快照，强制重新走 OAuth
                      （换账号、或快照异常时用它）。
        """
        client = self._get_client()
        self._login_solve_mode = solve_mode
        self._login_human_timeout = human_timeout

        if not force and self.backend == "browser":
            try:
                if client.try_restore_session():
                    self._login_time = time.time()
                    self._last_ok = time.time()
                    self._session_source = "restored"
                    return self.session_status()
            except Exception as e:  # noqa: BLE001
                logger.info("[AgentSession] 快照复用不可用，转正常登录：%s", e)

        self._login(client, solve_mode=solve_mode, human_timeout=human_timeout)
        self._session_source = "oauth"
        return self.session_status()

    def reset_cooldown(self) -> dict:
        """强制清除限频冷却窗口（人工确认站点已恢复后调用）。"""
        self._guard.reset()
        return self.session_status()

    def session_status(self) -> dict:
        age = None
        if self._login_time:
            age = int(time.time() - self._login_time)
        status = {
            "logged_in": self._client.logged_in if self._client else False,
            "age_sec": age,
            "backend": self.backend,
            "last_ok_sec": int(time.time() - self._last_ok) if self._last_ok else None,
            "heartbeat_interval": self.heartbeat_interval,
            # restrored=复用快照（没点验证码）；oauth=本次刚走完登录
            "session_source": self._session_source,
            "cooldown": self._guard.status(),
        }
        if self.backend == "browser" and self._client is not None:
            bb = getattr(self._client, "_browser_backend", None)
            if bb is not None:
                status["session_state_path"] = bb.session_state_path
        return status

    # ------------------------------------------------------------------ #
    # 保活心跳
    # ------------------------------------------------------------------ #
    def start_heartbeat(self) -> None:
        if self.heartbeat_interval and self.heartbeat_interval > 0 \
                and self._heartbeat_thread is None:
            self._stop_heartbeat = False
            self._heartbeat_thread = threading.Thread(
                target=self._hb_loop, daemon=True, name="wenshu-hb"
            )
            self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._stop_heartbeat = True
        self._heartbeat_thread = None

    def _hb_loop(self) -> None:
        while not self._stop_heartbeat:
            time.sleep(self.heartbeat_interval)
            try:
                self._ping()
            except Exception:  # noqa: BLE001
                pass

    def _ping(self) -> bool:
        client = self._client
        if client is None:
            return False
        if self.backend == "browser" and client._browser_backend is not None:
            return client._browser_backend.ping()
        # requests 后端：轻量首页请求拉伸 TTL
        try:
            client.limiter.acquire()
            client.session.get(
                client._headers.get("Origin", "https://wenshu.court.gov.cn/"),
                timeout=client.timeout, verify=client.verify,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        self.stop_heartbeat()
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


# --------------------------------------------------------------------------- #
# 模块级单例（进程内唯一会话）
# --------------------------------------------------------------------------- #
_SESSION: Optional[AgentSession] = None
_SESSION_LOCK = threading.Lock()


def get_session(backend: str = "browser", **kwargs) -> AgentSession:
    """返回进程内唯一的 AgentSession（首次调用时创建并启动心跳）。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = AgentSession(backend=backend, **kwargs)
            _SESSION.start_heartbeat()
        return _SESSION


def reset_session() -> None:
    """销毁当前单例（用于测试或重建）。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            _SESSION.close()
        _SESSION = None
