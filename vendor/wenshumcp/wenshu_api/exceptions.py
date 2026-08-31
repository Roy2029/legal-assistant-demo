"""自定义异常体系。

所有对外抛出的错误都继承自 WenshuError，便于调用方统一捕获。
反爬相关错误（验证码、频率限制、封锁）单独成类，方便上层做差异化处理。
"""

from __future__ import annotations


class WenshuError(Exception):
    """所有 wenshu 接口错误的基类。"""


class NetworkError(WenshuError):
    """网络层错误：连接失败、超时（剔除重试后仍失败的情况）。"""


class RateLimitError(WenshuError):
    """触发频率限制 / 被临时封锁。

    携带 retry_after（建议等待秒数），供调用方做退避。
    """

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class CaptchaRequiredError(WenshuError):
    """需要人工/第三方介入处理验证码。

    携带 captcha_image（图片字节或 URL）与 captcha_id，供上层弹窗或
    接入打码平台。本库不内置验证码自动破解能力。
    """

    def __init__(
        self,
        message: str,
        captcha_image: bytes | str | None = None,
        captcha_id: str | None = None,
    ):
        super().__init__(message)
        self.captcha_image = captcha_image
        self.captcha_id = captcha_id


class CaptchaUnavailableError(WenshuError):
    """验证码接口本身不可用（如返回 HTML 错误页/被拦截/地址已变更）。

    与 CaptchaRequiredError 不同：这类失败重试无意义，应直接上报并提示
    校准接口地址 / 请求头 / Cookie。
    """

    def __init__(self, message: str, body: bytes | str | None = None):
        super().__init__(message)
        self.body = body


class SessionExpiredError(WenshuError):
    """会话失效（vjkl5 / Cookie 过期，需重新初始化会话）。"""


class ParseError(WenshuError):
    """响应解析失败（接口返回结构变化或非预期内容）。"""


class DocumentNotFoundError(WenshuError):
    """指定文书 ID 不存在或无权访问。"""
