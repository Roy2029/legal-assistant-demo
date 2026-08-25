"""chat API 冒烟测试（不配置 LLM 时应返回明确错误事件）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"]
    print("PASS health")

def test_config_get_put():
    r = client.get("/api/config")
    assert r.status_code == 200 and r.json()["ok"]
    r = client.put("/api/config", json={"llm": {"base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}})
    assert r.status_code == 200 and r.json()["ok"]
    print("PASS config_get_put")

def test_chat_empty_query():
    r = client.post("/api/chat", json={"query": ""})
    assert r.status_code == 200 and r.json()["ok"] is False
    print("PASS chat_empty_query")

if __name__ == "__main__":
    test_health(); test_config_get_put(); test_chat_empty_query()
    print("ALL CHAT API TESTS PASSED")
