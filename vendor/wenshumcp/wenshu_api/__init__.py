"""wenshu_api：中国裁判文书网爬虫 API 封装。

公开导出：
    WenshuClient      主客户端
    SearchResult / DocumentMeta / DatabaseStructure / CourtNode / FieldMeta  数据模型
    WenshuError 及子类   异常
"""

from __future__ import annotations

from .client import WenshuClient
from .exceptions import (
    CaptchaRequiredError,
    DocumentNotFoundError,
    NetworkError,
    ParseError,
    RateLimitError,
    SessionExpiredError,
    WenshuError,
)
from .models import (
    CourtNode,
    DatabaseStructure,
    DocumentMeta,
    FieldMeta,
    SearchResult,
)

__all__ = [
    "WenshuClient",
    "SearchResult",
    "DocumentMeta",
    "DatabaseStructure",
    "CourtNode",
    "FieldMeta",
    "WenshuError",
    "NetworkError",
    "RateLimitError",
    "CaptchaRequiredError",
    "SessionExpiredError",
    "ParseError",
    "DocumentNotFoundError",
]

__version__ = "0.1.0"
