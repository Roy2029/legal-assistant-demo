"""类案检索 agent 测试（D09 阶段7）：用 fake adapter 验证工具调用与渐进式读取入口。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.agents.case_agent import CaseAgent


class FakeAdapter:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_judgments_tool":
            return {"ok": True, "tool": name, "result": json.dumps({"total": 2, "documents": [{"案号": "(2024)京01民初1号", "案件名称": "测试案"}]}, ensure_ascii=False)}
        if name == "get_judgment_by_case_number_tool":
            return {"ok": True, "tool": name, "result": "文书正文（本院认为...）"}
        return {"ok": False, "tool": name, "error": "unknown"}


def test_case_agent_run():
    async def _run():
        fake = FakeAdapter()
        agent = CaseAgent(adapter=fake)
        result = await agent.run("建设工程 实际施工人")
        assert result["ok"] is True
        assert result["total"] == 2
        assert result["documents"][0]["案号"] == "(2024)京01民初1号"
        assert fake.calls[0][0] == "search_judgments_tool"
        # 渐进式读取入口
        r = await agent.read("(2024)京01民初1号")
        assert r["ok"] is True and "本院认为" in r["result"]
        print("PASS case_agent_run")
    asyncio.run(_run())


def test_case_agent_tools_schema():
    tools = CaseAgent.tools()
    names = [t["function"]["name"] for t in tools]
    assert "case_search" in names and "case_read" in names
    print("PASS case_agent_tools_schema")


if __name__ == "__main__":
    test_case_agent_run()
    test_case_agent_tools_schema()
    print("ALL CASE AGENT TESTS PASSED")
