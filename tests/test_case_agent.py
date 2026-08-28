"""类案检索 agent 测试（D09 阶段7）。"""
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


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return {"content": "", "tool_calls": []}


def test_case_agent_direct_methods():
    async def _run():
        fake = FakeAdapter()
        agent = CaseAgent(adapter=fake)
        r = await agent.search(fulltext_keyword="建设工程 实际施工人", page_num=1, page_size=15)
        assert r["ok"] is True
        r2 = await agent.read("(2024)京01民初1号")
        assert r2["ok"] is True and "本院认为" in r2["result"]
        print("PASS case_agent_direct_methods")
    asyncio.run(_run())


def test_case_agent_tools_schema():
    tools = CaseAgent().tools()
    names = [t["function"]["name"] for t in tools]
    assert "case_search" in names and "case_read" in names and "finish" in names
    print("PASS case_agent_tools_schema")


def test_case_agent_react_finish():
    async def _run():
        fake_llm = FakeLLM([
            {"content": "我先查类案。", "tool_calls": [{"id": "c1", "name": "case_search", "arguments": {"fulltext_keyword": "实际施工人"}}]},
            {"content": "已找到类案，提交报告。", "tool_calls": [{"id": "c2", "name": "finish", "arguments": {"report": "# 类案报告", "answer": "有类案支持。", "citations": [{"case_number": "(2024)京01民初1号", "case_name": "测试案"}], "needs_human": False}}]},
        ])
        agent = CaseAgent(adapter=FakeAdapter(), llm=fake_llm, session_id="case-test")
        result = await agent.run("实际施工人 类案")
        assert result["ok"] is True
        assert result["answer"] == "有类案支持。"
        assert result["citations"][0]["case_number"] == "(2024)京01民初1号"
        print("PASS case_agent_react_finish")
    asyncio.run(_run())


if __name__ == "__main__":
    test_case_agent_direct_methods()
    test_case_agent_tools_schema()
    test_case_agent_react_finish()
    print("ALL CASE AGENT TESTS PASSED")
