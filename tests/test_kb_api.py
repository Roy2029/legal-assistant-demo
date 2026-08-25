"""W4 用户知识库 API 测试：上传→列表→隔离检索→删除。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

TEST_MD = Path("D:/个人/legal-assistant-demo/data/test_upload.md")

client = TestClient(app)

def test_upload_list_retrieve_delete():
    # 1. 上传
    with open(TEST_MD, "rb") as f:
        r = client.post("/api/kb/upload", files={"file": ("test_upload.md", f, "text/markdown")})
    assert r.status_code == 200 and r.json()["ok"], r.text
    doc_id = r.json()["data"]["doc_id"]
    assert r.json()["data"]["children"] >= 1

    # 2. 列表
    r = client.get("/api/kb/docs")
    assert r.json()["ok"]
    docs = [d for d in r.json()["data"] if d["doc_id"] == doc_id]
    assert len(docs) == 1

    # 3. 用户库隔离检索（scope=user 应命中用户文档；复用单例避免 Qdrant 锁冲突）
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    out = svc.search("连续旷工三日", corpus_scope="user")
    assert len(out.results) > 0, "用户库检索应命中上传文档"
    assert any(r.chunk.metadata.get("corpus") == "user" for r in out.results)

    # 4. 删除
    r = client.delete(f"/api/kb/docs/{doc_id}")
    assert r.status_code == 200 and r.json()["ok"]
    out2 = svc.search("连续旷工三日", corpus_scope="user")
    assert all(r.chunk.metadata.get("corpus") != "user" for r in out2.results) or len(out2.results) == 0
    print("PASS upload_list_retrieve_delete")

if __name__ == "__main__":
    test_upload_list_retrieve_delete()
    print("ALL KB API TESTS PASSED")
