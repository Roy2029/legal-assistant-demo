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
    # PUT 测试后立即清理，避免污染 .env 真实配置
    r = client.put("/api/config", json={"llm": {"base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}})
    assert r.status_code == 200 and r.json()["ok"]
    from pathlib import Path
    Path("D:/个人/legal-assistant-demo/data/config.json").unlink(missing_ok=True)
    from server.config_service import config_service
    config_service._cache = None
    print("PASS config_get_put")

def test_chat_empty_query():
    r = client.post("/api/chat", json={"query": ""})
    assert r.status_code == 200 and r.json()["ok"] is False
    print("PASS chat_empty_query")

if __name__ == "__main__":
    test_health(); test_config_get_put(); test_chat_empty_query()
    print("ALL CHAT API TESTS PASSED")
