"""限流工具：令牌桶实现，防止请求过快触发站点频率限制。"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """简单令牌桶限流器。

    用法：
        limiter = RateLimiter(max_qps=1.0)   # 每秒最多 1 个请求
        limiter.acquire()                    # 阻塞直到拿到令牌

    也可通过 min_interval 限制最小请求间隔（秒），更直观：
        RateLimiter(min_interval=2.0)        # 两次请求至少间隔 2 秒
    """

    def __init__(self, max_qps: float | None = None, min_interval: float | None = None):
        if max_qps is not None:
            self._rate = max_qps
        elif min_interval is not None:
            self._rate = 1.0 / max(min_interval, 1e-6)
        else:
            self._rate = 1.0  # 默认 1 QPS

        self._capacity = max(self._rate, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """获取 tokens 个令牌，不足时阻塞等待。"""
        with self._lock:
            while True:
                now = time.monotonic()
                # 按时间补充令牌
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # 计算需要等待的时间
                wait = (tokens - self._tokens) / self._rate
                time.sleep(max(wait, 0.0))


def make_limiter(max_qps: float | None = None, min_interval: float | None = None) -> RateLimiter:
    return RateLimiter(max_qps=max_qps, min_interval=min_interval)
