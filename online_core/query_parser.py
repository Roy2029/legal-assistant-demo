"""Query 解析器（D02 §3.3）：规则+法规名词典，提取结构化过滤条件，零 LLM。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 效力级别词
EFFECT_LEVELS = {
    "法律": ["法律", "全国人大", "全国人大常委会"],
    "行政法规": ["行政法规", "国务院"],
    "司法解释": ["司法解释", "最高法", "最高检", "最高人民法院", "最高人民检察院"],
    "部门规章": ["部门规章", "规章"],
    "地方性法规": ["地方性法规"],
}

# 否定/排除词
NEGATION_WORDS = ["不是", "并非", "除了", "除…外", "排除", "而非", "不包括", "不含", "不属于"]

# 常用法规名简称映射（M0 内置；后续从 law_meta 加载补充）
LAW_ALIASES = {
    "民法典": ["中华人民共和国民法典", "民法典"],
    "民诉法": ["中华人民共和国民事诉讼法", "民事诉讼法"],
    "刑诉法": ["中华人民共和国刑事诉讼法", "刑事诉讼法"],
    "刑法": ["中华人民共和国刑法"],
    "劳动法": ["中华人民共和国劳动法"],
    "劳动合同法": ["中华人民共和国劳动合同法"],
    "劳动争议调解仲裁法": ["中华人民共和国劳动争议调解仲裁法"],
    "建工司法解释一": ["最高人民法院关于审理建设工程施工合同纠纷案件适用法律问题的解释（一）"],
    "公司法": ["中华人民共和国公司法"],
    "行政诉讼法": ["中华人民共和国行政诉讼法"],
}

ARTICLE_RE = re.compile(r"第([一二三四五六七八九十百零\d]+)条")


@dataclass
class ParsedQuery:
    original_query: str
    law_name: Optional[str] = None
    article_no: Optional[str] = None
    effect_level: Optional[str] = None
    filter: dict = field(default_factory=dict)
    exact_match: bool = False
    excluded: list = field(default_factory=list)


def _find_law_name(query: str) -> Optional[str]:
    """在 query 中查找已知法规名（别名最长匹配优先）。"""
    hits = []
    for canonical, aliases in LAW_ALIASES.items():
        for alias in aliases:
            idx = query.find(alias)
            if idx >= 0:
                hits.append((len(alias), canonical, alias, idx))
    if not hits:
        return None
    hits.sort(key=lambda x: (-x[0], x[3]))  # 最长别名优先
    return hits[0][1]


def _extract_articles_with_context(query: str):
    """提取所有 第N条 及其前 5 字符语境。"""
    out = []
    for m in ARTICLE_RE.finditer(query):
        start = max(0, m.start() - 5)
        context = query[start:m.start()]
        out.append({"article_no": m.group(1), "context": context, "start": m.start()})
    return out


def _find_effect_level(query: str) -> Optional[str]:
    for level, words in EFFECT_LEVELS.items():
        for w in words:
            if w in query:
                return level
    return None


def parse_query(query: str) -> ParsedQuery:
    """解析 query，返回结构化查询对象。"""
    pq = ParsedQuery(original_query=query)
    f = {}

    # 1) 效力级别
    effect = _find_effect_level(query)
    if effect:
        pq.effect_level = effect
        f["effect_level"] = effect

    # 2) 法条号候选 + 否定排除
    law_name = _find_law_name(query)
    articles = _extract_articles_with_context(query)
    candidates = []
    for a in articles:
        negated = any(nw in a["context"] for nw in NEGATION_WORDS)
        if negated:
            pq.excluded.append({"article_no": a["article_no"], "law_name": law_name})
        else:
            candidates.append(a)

    # 3) 精确匹配构造
    if candidates:
        if law_name:
            pq.law_name = law_name
            f["law_name"] = law_name
            if len(candidates) == 1:
                pq.article_no = candidates[0]["article_no"]
                f["article_no"] = candidates[0]["article_no"]
                pq.exact_match = True
            else:
                # 多候选同法规：article_no 多值（Qdrant OR）
                f["article_no"] = [c["article_no"] for c in candidates]
                pq.article_no = f["article_no"]
                pq.exact_match = True  # 多值精确匹配
        else:
            if len(candidates) == 1:
                pq.article_no = candidates[0]["article_no"]
                f["article_no"] = candidates[0]["article_no"]
                pq.exact_match = True
            else:
                pq.article_no = [c["article_no"] for c in candidates]
                pq.exact_match = True

    if f:
        pq.filter = f
    return pq
