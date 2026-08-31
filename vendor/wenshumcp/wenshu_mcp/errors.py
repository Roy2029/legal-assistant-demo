"""MCP 工具层的统一结果结构与错误码。

设计目标：所有工具都返回 ``{"ok": bool, "error_code": str, "data"|"message": ...}``，
不让上层 agent 去解析异常字符串，而是根据 error_code 自动决策（重试 / 重登 / 转人工）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    OK = "OK"
    NEED_SETUP = "NEED_SETUP"          # 缺依赖 / chromium / 凭据 → 指向 health_check
    CAPTCHA_FAILED = "CAPTCHA_FAILED"  # 验证码识别重试耗尽 → 建议人工 / 换时段
    SESSION_EXPIRED = "SESSION_EXPIRED"  # 已自动重登仍失败 → 转人工
    WAF_SOFT_BLOCK = "WAF_SOFT_BLOCK"  # code=1 但 0 命中软拦截 → 内部已切浏览器后端
    SITE_DRIFT = "SITE_DRIFT"          # 算法 / 协议疑似失效 → 建议更新算法配置
    RATE_LIMITED = "RATE_LIMITED"      # 触发限流 → 退避重试
    DOC_NOT_FOUND = "DOC_NOT_FOUND"    # 文书不存在 / 无权访问
    UNKNOWN = "UNKNOWN"


def ok(data: Any = None) -> dict:
    return {"ok": True, "error_code": ErrorCode.OK.value, "data": data}


def fail(code: ErrorCode, message: str, extra: Optional[dict] = None) -> dict:
    r: dict = {"ok": False, "error_code": code.value, "message": message}
    if extra:
        r.update(extra)
    return r


def map_exception(exc: Exception) -> dict:
    """把底层异常映射成结构化错误结果。"""
    from wenshu_api.exceptions import (  # 局部导入，避免循环依赖
        CaptchaRequiredError,
        CaptchaUnavailableError,
        SessionExpiredError,
        DocumentNotFoundError,
        RateLimitError,
        WenshuError,
        NetworkError,
        ParseError,
    )

    if isinstance(exc, SessionExpiredError):
        return fail(ErrorCode.SESSION_EXPIRED, str(exc))
    if isinstance(exc, (CaptchaRequiredError, CaptchaUnavailableError)):
        return fail(ErrorCode.CAPTCHA_FAILED, str(exc))
    if isinstance(exc, DocumentNotFoundError):
        return fail(ErrorCode.DOC_NOT_FOUND, str(exc))
    if isinstance(exc, RateLimitError):
        return fail(ErrorCode.RATE_LIMITED, str(exc),
                    {"retry_after": getattr(exc, "retry_after", None)})
    if isinstance(exc, (NetworkError, ParseError, WenshuError)):
        msg = str(exc)
        if "没有权限" in msg or "登录态" in msg or "校验通过" in msg:
            return fail(ErrorCode.SESSION_EXPIRED, msg)
        return fail(ErrorCode.SITE_DRIFT, msg)
    return fail(ErrorCode.UNKNOWN, str(exc))
