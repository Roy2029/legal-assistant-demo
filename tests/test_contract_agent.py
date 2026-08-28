"""合同审查 agent ReAct 测试（D10 v1.1）。"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.agents.contract_agent import ContractAgent


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return {"content": "", "tool_calls": []}


def test_contract_agent_finish_flow():
    async def _run():
        cid = "test-contract-agent"
        ws = Path("data/agent_workspace/contract-" + cid + "/contracts")
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "合同.md").write_text("甲方：张三。逾期按日千分之一支付违约金。", encoding="utf-8")
        fake = FakeLLM([
            {"content": "先查看合同文件。", "tool_calls": [{"id": "c1", "name": "list_contracts", "arguments": {}}]},
            {"content": "读取合同。", "tool_calls": [{"id": "c2", "name": "read_contract", "arguments": {"file": "合同.md"}}]},
            {"content": "扫描风险。", "tool_calls": [{"id": "c3", "name": "check_rules", "arguments": {"text": "甲方：张三。逾期按日千分之一支付违约金。"}}]},
            {"content": "完成。", "tool_calls": [{"id": "c4", "name": "finish", "arguments": {"report": "# 报告\n", "answer": "发现1处风险", "risks": [{"rule_id": "cr-builtin-002", "risk_level": "high"}], "needs_human": False}}]},
        ])
        agent = ContractAgent(contract_id=cid, llm=fake)
        result = await agent.run("审查合同")
        assert result["ok"] is True
        assert result["answer"] == "发现1处风险"
        assert result["risks"][0]["rule_id"] == "cr-builtin-002"
        assert len(agent.trace["rounds"]) >= 3
        print("PASS contract_agent_finish_flow")
        shutil.rmtree(ws.parent, ignore_errors=True)
    asyncio.run(_run())


if __name__ == "__main__":
    test_contract_agent_finish_flow()
    print("ALL CONTRACT AGENT TESTS PASSED")
