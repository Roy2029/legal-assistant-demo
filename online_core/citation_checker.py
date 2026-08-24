"""引用校验器（D02 §7）：抽取条文引用并精确校验，防法条幻觉。

M0 校验数据源：Qdrant 新索引 payload（metadata.law_name + metadata.article_no）。
预留 law_meta SQLite 校验接口（填充后切换）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .query_parser import LAW_ALIASES

CN_NUM = "一二三四五六七八九十百零千"

ARTICLE_PAT = re.compile(
    r"([\u4e00-\u9fff]{2,30}?)(?:第([一二三四五六七八九十百零千\d]+)条)"
)

STATUS_VALID = "现行有效"


def _cn_to_arabic(cn: str) -> str:
    from offline_core.docx_parser import chinese_to_arabic
    try:
        return str(chinese_to_arabic(cn))
    except Exception:
        return cn


def _canonical_law_name(name: str) -> Optional[str]:
    """把法规名映射到规范名（元数据中存文件名提取的全称）。"""
    if name in LAW_ALIASES:
        return LAW_ALIASES[name][0]
    # 直接匹配元数据里的全称
    return name


@dataclass
class Citation:
    law_name: str
    article_no: str
    raw: str


@dataclass
class CitationCheckResult:
    verified: list = field(default_factory=list)
    expired: list = field(default_factory=list)
    unverifiable: list = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return not self.unverifiable and not self.expired


def extract_citations(text: str) -> list[Citation]:
    """从文本抽取 法规名+第X条 引用（优先按 LAW_ALIASES 精确匹配）。"""
    out = []
    # 构造 别名|全称 的匹配模式，长名在前
    names = []
    for canonical, aliases in LAW_ALIASES.items():
        for a in aliases:
            names.append(a)
    names.sort(key=len, reverse=True)
    if names:
        name_pat = "|".join(re.escape(n) for n in names)
        pat = re.compile(rf"({name_pat})(?:第([一二三四五六七八九十百零千\d]+)条)")
        for m in pat.finditer(text):
            raw = m.group(0)
            law = m.group(1)
            article = m.group(2)
            if article and not article.isdigit():
                article = _cn_to_arabic(article)
            out.append(Citation(law_name=law, article_no=article, raw=raw))
    # 去重
    seen = set()
    unique = []
    for c in out:
        k = (c.law_name, c.article_no)
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


class CitationChecker:
    def __init__(self, index_path: str | None = None):
        self.index_path = index_path or str(
            Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")
        )
        self._client = None

    def _get_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(path=self.index_path)
        return self._client

    def verify(self, text: str) -> CitationCheckResult:
        citations = extract_citations(text)
        result = CitationCheckResult()
        for c in citations:
            law = _canonical_law_name(c.law_name)
            if law is None:
                result.unverifiable.append(c)
                continue
            if self._exists(law, c.article_no):
                result.verified.append(c)
            else:
                result.unverifiable.append(c)
        return result

    def _exists(self, law_name: str, article_no: str) -> bool:
        try:
            from qdrant_client import models
            client = self._get_client()
            f = models.Filter(
                must=[
                    models.FieldCondition(key="metadata.law_name", match=models.MatchValue(value=law_name)),
                    models.FieldCondition(key="metadata.articles", match=models.MatchAny(any=[article_no])),
                ]
            )
            pts, _ = client.scroll(collection_name="chunks", scroll_filter=f, limit=1)
            return len(pts) > 0
        except Exception:
            return False

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
