"""badcase 反馈 API 测试：提交→列表→改状态。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def test_badcase_flow():
    r = client.post("/api/badcases", json={
        "mode": "chat",
        "session_id": "test-session",
        "trace_id": "test-trace",
        "query": "测试问题",
        "answer": "测试回答",
        "reason": "retrieval",
        "note": "测试备注",
        "trace": {"final_topk": [{"chunk_id": "chunk:test"}]},
    })
    assert r.status_code == 200 and r.json()["ok"], r.text
    bid = r.json()["data"]["id"]

    r = client.get("/api/badcases?status=new&limit=10")
    assert r.json()["ok"]
    ids = [d["id"] for d in r.json()["data"]]
    assert bid in ids

    r = client.put(f"/api/badcases/{bid}", json={"status": "pending", "root_cause": "检索排序问题"})
    assert r.status_code == 200 and r.json()["ok"]

    r = client.get("/api/badcases/summary")
    assert r.json()["ok"]
    print("PASS badcase_flow")


if __name__ == "__main__":
    test_badcase_flow()
    print("ALL BADCASE API TESTS PASSED")
