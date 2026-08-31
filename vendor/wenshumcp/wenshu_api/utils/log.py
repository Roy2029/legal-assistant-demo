"""统一日志：让成功/失败/重试/刷新等状态可被观测。

用法：
    from wenshu_api.utils.log import configure_logging, get_logger
    configure_logging(level=logging.INFO)   # 在程序入口调用一次
    log = get_logger()
    log.info("[成功] 命中 %d 条", n)

默认只给 wenshu_api 这个 logger 挂一个 stderr handler，不影响使用方自己的日志。
"""

from __future__ import annotations

import logging

_LOGGER_NAME = "wenshu_api"


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """配置 wenshu_api 的日志输出（仅首次生效）。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter(
            "%(asctime)s [wenshu] %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False  # 不向 root 冒泡，避免重复输出
    return logger
