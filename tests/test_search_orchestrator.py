"""检索编排层单元测试（D09 阶段3）：fuse 融合与 separate 结构。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_core.data_model import Chunk, RetrievalResult
from online_core.search_orchestrator import _rrf_fuse, _chunk_to_dict


def make_result(chunk_id, score=1.0):
    c = Chunk(chunk_id=chunk_id, doc_id="d", text=f"text-{chunk_id}", metadata={"law_name": "法"}, block_ids=[], order=0, chunk_level="child")
    return RetrievalResult(chunk=c, score=score, retrieval_type="rrf")


def test_rrf_fuse_dedup_and_order():
    r1 = [make_result("c1", 0.9), make_result("c2", 0.5)]
    r2 = [make_result("c2", 0.8), make_result("c3", 0.4)]
    fused = _rrf_fuse({"q1": r1, "q2": r2}, top_k=3)
    ids = [d["chunk_id"] for d in fused]
    assert len(ids) == 3
    assert ids[0] == "c2"  # 两个列表都出现，RRF 分更高
    assert "c1" in ids and "c3" in ids
    # 来源 query 记录
    c2 = fused[0]
    assert "q1" in c2["from_query"] and "q2" in c2["from_query"]
    print("PASS rrf_fuse_dedup_and_order")


def test_chunk_to_dict_truncates_text():
    c = Chunk(chunk_id="c1", doc_id="d", text="很" * 500, metadata={"law_name": "测试法"}, block_ids=[], order=0, chunk_level="child")
    d = _chunk_to_dict(RetrievalResult(chunk=c, score=1.0, retrieval_type="rrf"), "q")
    assert len(d["text"]) == 400
    assert d["meta"]["law_name"] == "测试法"
    assert d["from_query"] == "q"
    print("PASS chunk_to_dict_truncates_text")


if __name__ == "__main__":
    test_rrf_fuse_dedup_and_order()
    test_chunk_to_dict_truncates_text()
    print("ALL ORCHESTRATOR TESTS PASSED")
