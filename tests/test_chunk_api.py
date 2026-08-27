"""chunk API 测试：全文读取与法条定位（走共享检索服务单例，不新建 QdrantClient）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def test_get_chunk_full_text():
    # 先通过检索服务拿到一个真实 chunk_id
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    out = svc.search("民法典第580条")
    assert len(out.results) > 0, "应有检索结果"
    cid = out.results[0].chunk.chunk_id
    r = client.get(f"/api/chunk/{cid}")
    assert r.status_code == 200 and r.json()["ok"], r.text
    data = r.json()["data"]
    assert data["chunk_id"] == cid
    assert len(data["text"]) > 120, "chunk API 应返回完整原文，而非截断预览"
    print("PASS get_chunk_full_text")


def test_locate():
    r = client.get("/api/chunk/locate", params={"law_name": "民法典", "article_no": "580"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    data = r.json()["data"]
    assert "580" in (data.get("article_no") or ""), data
    assert len(data["text"]) > 0
    print("PASS locate")


if __name__ == "__main__":
    test_get_chunk_full_text()
    test_locate()
    print("ALL CHUNK API TESTS PASSED")
