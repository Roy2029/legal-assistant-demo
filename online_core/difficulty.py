"""DifficultyEstimator 规则版（D02 §4.2）：不依赖 LLM Router。"""
from __future__ import annotations

from .query_parser import ParsedQuery, parse_query

COMPARE_WORDS = ["对比", "区别", "异同", "分别", "比较"]
PROCEDURE_WORDS = ["哪些情形", "如何认定", "程序", "步骤", "条件", "要件", "举证责任"]


def estimate(query: str) -> dict:
    """返回 {level, rule_hit, top_k}。"""
    pq: ParsedQuery = parse_query(query)

    if pq.exact_match:
        return {"level": "simple", "rule_hit": "exact_match", "top_k": 5}
    if any(w in query for w in COMPARE_WORDS):
        return {"level": "hard", "rule_hit": "compare_words", "top_k": 10}
    if query.count("？") + query.count("?") >= 2 or len(query) > 40:
        return {"level": "hard", "rule_hit": "multi_question_or_long", "top_k": 10}
    if any(w in query for w in PROCEDURE_WORDS):
        return {"level": "medium", "rule_hit": "procedure_words", "top_k": 8}
    return {"level": "medium", "rule_hit": "default", "top_k": 8}
