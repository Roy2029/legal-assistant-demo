"""RAG agent ReAct 循环测试（D09 阶段4）：使用 mock LLM 验证工具循环与 finish。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.agents.rag_agent import RAGAgent


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return {"content": "", "tool_calls": []}


def test_rag_agent_finish_flow():
    async def _run():
        fake = FakeLLM([
            {
                "content": "我先查看知识库概览。",
                "tool_calls": [{"id": "c1", "name": "kb_index", "arguments": {}}],
            },
            {
                "content": "检索完成，提交报告。",
                "tool_calls": [{
                    "id": "c2", "name": "finish",
                    "arguments": {
                        "report": "# 报告\n查询过程略",
                        "answer": "可以。",
                        "citations": [{"law_name": "民法典", "article_no": "580", "chunk_id": "chunk:test"}],
                        "needs_human": False,
                    },
                }],
            },
        ])
        agent = RAGAgent(llm=fake, session_id="test-agent")
        result = await agent.run("测试问题")
        assert result["ok"] is True
        assert result["answer"] == "可以。"
        assert result["citations"][0]["article_no"] == "580"
        assert len(agent.trace["rounds"]) >= 1
        assert agent.trace["rounds"][0]["think_present"] is True
        print("PASS rag_agent_finish_flow")
    asyncio.run(_run())


def test_rag_agent_no_tool_calls_ends():
    async def _run():
        fake = FakeLLM([{"content": "直接回答，无需检索。", "tool_calls": []}])
        agent = RAGAgent(llm=fake, session_id="test-agent")
        result = await agent.run("简单问题")
        assert result["ok"] is True
        assert result["answer"] == "直接回答，无需检索。"
        print("PASS rag_agent_no_tool_calls_ends")
    asyncio.run(_run())


if __name__ == "__main__":
    test_rag_agent_finish_flow()
    test_rag_agent_no_tool_calls_ends()
    print("ALL RAG AGENT TESTS PASSED")
