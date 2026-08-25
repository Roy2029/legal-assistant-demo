"""Query 解析器单元测试（D02 §3.3）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.query_parser import parse_query


def test_exact_article():
    q = parse_query("民法典第580条说了什么")
    assert q.exact_match is True
    assert q.law_name == "中华人民共和国民法典"
    assert q.article_no == "580"
    assert q.filter["law_name"] == "中华人民共和国民法典"
    assert q.filter["article_no"] == "580"
    print("PASS exact_article")


def test_effect_level():
    q = parse_query("司法解释关于实际施工人怎么规定")
    assert q.effect_level == "司法解释"
    assert q.filter["effect_level"] == "司法解释"
    assert q.exact_match is False
    print("PASS effect_level")


def test_negation_excluded():
    q = parse_query("不是民法典第580条，而是民法典第590条")
    assert q.exact_match is True
    assert q.article_no == "590"
    assert len(q.excluded) == 1 and q.excluded[0]["article_no"] == "580"
    print("PASS negation_excluded")


def test_multi_candidate():
    q = parse_query("民法典第580条还是第590条")
    assert q.exact_match is True
    assert q.article_no == ["580", "590"] or q.article_no == ["590", "580"]
    print("PASS multi_candidate")


def test_no_match():
    q = parse_query("实际施工人能否向发包人主张工程款")
    assert q.exact_match is False
    assert q.filter == {}
    print("PASS no_match")


if __name__ == "__main__":
    test_exact_article()
    test_effect_level()
    test_negation_excluded()
    test_multi_candidate()
    test_no_match()
    print("ALL QUERY PARSER TESTS PASSED")
