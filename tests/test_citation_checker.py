import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from online_core.citation_checker import extract_citations

def test_extract_alias():
    cs = extract_citations("根据民法典第580条，违约方可以请求终止合同。")
    assert cs and cs[0].law_name == "民法典" and cs[0].article_no == "580", [(c.law_name, c.article_no) for c in cs]
    print("PASS extract_alias")

def test_extract_cn_article():
    cs = extract_citations("依据中华人民共和国民法典第五百八十条处理")
    assert cs and cs[0].article_no == "580", [(c.law_name, c.article_no) for c in cs]
    print("PASS extract_cn_article")

def test_extract_none():
    cs = extract_citations("今天天气不错")
    assert cs == []
    print("PASS extract_none")

if __name__ == "__main__":
    test_extract_alias(); test_extract_cn_article(); test_extract_none()
    print("ALL CITATION EXTRACT TESTS PASSED")
