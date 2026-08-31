"""重试装饰器：网络超时 / 瞬时错误自动退避重试。"""

from __future__ import annotations

import functools
import random
import time

from ..exceptions import NetworkError, RateLimitError
from .log import get_logger


def retry(
    max_retries: int = 3,
    backoff: float = 1.0,
    jitter: float = 0.5,
    retry_on: tuple[type[Exception], ...] = (NetworkError,),
):
    """指数退避重试。

    参数：
        max_retries: 最大重试次数（不含首次）。
        backoff:     基础退避秒数，第 n 次等待 ≈ backoff * 2**n。
        jitter:      随机抖动上限（秒），避免请求同步。
        retry_on:    需要触发重试的异常类型。

    注意：RateLimitError 默认不在此处重试（应交由调用方做长退避或换会话），
    如确需自动重试，把它加入 retry_on 并通过 retry_after 控制等待。
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = get_logger()
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= max_retries:
                        break
                    wait = backoff * (2 ** attempt) + random.uniform(0, jitter)
                    log.warning(
                        "[重试] 网络错误（%s），第 %d/%d 次，%.1fs 后重试",
                        type(exc).__name__, attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                except RateLimitError:
                    # 频率限制：不做短重试，直接上抛
                    raise
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
