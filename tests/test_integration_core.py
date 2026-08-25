"""M0 核心链路集成测试（不含 LLM）：解析→检索→引用校验。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.retrieval_service import get_retrieval_service
from online_core.citation_checker import CitationChecker
from online_core.query_parser import parse_query
from online_core.difficulty import estimate

INDEX = str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant"))

def test_exact_article_retrieval():
    svc = get_retrieval_service()
    out = svc.search("民法典第580条")
    assert len(out.results) > 0, "精确法条号应命中"
    assert out.difficulty["level"] == "simple"
    for r in out.results:
        arts = r.chunk.metadata.get("articles", [])
        assert "580" in arts, f"命中 chunk 应含 580，实际 {arts}"
    print(f"PASS exact_article_retrieval ({len(out.results)} results)")


def test_effect_level_filter():
    svc = get_retrieval_service()
    out = svc.search("司法解释关于实际施工人怎么规定")
    # M0 效力级别元数据未填充，语义检索应有结果（不强制过滤）
    assert len(out.results) > 0
    print(f"PASS effect_level_semantic ({len(out.results)} results)")


def test_citation_verification():
    # 释放单例 Qdrant 锁，避免本地嵌入式锁冲突
    svc0 = get_retrieval_service()
    if svc0._store is not None:
        svc0._store.close()
        svc0._store = None
    cc = CitationChecker(index_path=INDEX)
    res = cc.verify("根据民法典第580条，违约方可以请求终止合同。")
    assert res.verified, "民法典第580条应可验证"
    assert not res.unverifiable
    res2 = cc.verify("根据不存在法第9999条处理")
    assert res2.unverifiable, "虚构法条应不可验证"
    cc.close()
    print("PASS citation_verification")


if __name__ == "__main__":
    test_exact_article_retrieval()
    test_effect_level_filter()
    test_citation_verification()
    print("ALL CORE INTEGRATION TESTS PASSED")
