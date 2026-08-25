"""W5 实务助手 API 测试（M0 桩）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)

def test_actions():
    r = client.get("/api/actions")
    assert r.status_code == 200 and r.json()["ok"]
    ids = [a["skill_id"] for a in r.json()["data"]]
    assert "case_analysis" in ids and "contract_review" in ids and "legal_memo" in ids
    print("PASS actions")

def test_assistant_stream():
    with client.stream("POST", "/api/assistant", json={"action": "case_analysis", "query": "实际施工人能否向发包人主张工程款"}) as resp:
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines():
            if line and line.startswith("data:"):
                import json
                evt = json.loads(line[5:].strip())
                events.append(evt["type"])
        assert "step_start" in events
        assert "tool_call" in events
        assert "tool_result" in events
        assert "final" in events
        assert "done" in events
    print(f"PASS assistant_stream ({len(events)} events)")

def test_unknown_action():
    r = client.post("/api/assistant", json={"action": "nope", "query": "x"})
    assert r.status_code == 200
    print("PASS unknown_action")

if __name__ == "__main__":
    test_actions(); test_assistant_stream(); test_unknown_action()
    print("ALL ASSISTANT API TESTS PASSED")
