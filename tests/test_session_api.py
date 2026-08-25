import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)

def test_session_crud():
    r = client.post("/api/sessions", json={"title": "测试会话"})
    assert r.status_code == 200 and r.json()["ok"]
    sid = r.json()["data"]["session_id"]
    r = client.get("/api/sessions")
    ids = [s["session_id"] for s in r.json()["data"]]
    assert sid in ids
    r = client.get(f"/api/sessions/{sid}/messages")
    assert r.json()["ok"] and r.json()["data"] == []
    r = client.delete(f"/api/sessions/{sid}")
    assert r.json()["ok"]
    r = client.get("/api/sessions")
    ids = [s["session_id"] for s in r.json()["data"]]
    assert sid not in ids
    print("PASS session_crud")

if __name__ == "__main__":
    test_session_crud()
    print("ALL SESSION API TESTS PASSED")
