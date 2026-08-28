"""类案检索 agent（D09 §9）：ReAct 子代理，接裁判文书检索 MCP。"""
from __future__ import annotations

import asyncio
from typing import Optional

from online_core.agents.base import BaseReActAgent
from online_core.mcp.wenshu_adapter import WenshuMCPAdapter


class CaseAgent(BaseReActAgent):
    """类案检索 agent：case_search / case_read / case_search_by_law / case_guided。"""

    def __init__(self, adapter: Optional[WenshuMCPAdapter] = None, llm=None, session_id: str = "default", **kwargs):
        super().__init__(llm=llm, session_id=session_id, **kwargs)
        self.adapter = adapter or WenshuMCPAdapter()

    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "case_search",
                    "description": "检索裁判文书元数据（总数/案号/案件名称/案由/法院/裁判日期/理由摘要），不返回全文。至少提供一个检索条件。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fulltext_keyword": {"type": "string"},
                            "case_type": {"type": "string"},
                            "court_level": {"type": "string"},
                            "case_name": {"type": "string"},
                            "case_number": {"type": "string"},
                            "court_name": {"type": "string"},
                            "party_name": {"type": "string"},
                            "judgment_date_start": {"type": "string"},
                            "judgment_date_end": {"type": "string"},
                            "legal_basis": {"type": "string"},
                            "page_num": {"type": "integer", "default": 1},
                            "page_size": {"type": "integer", "default": 15},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_read",
                    "description": "按案号读取文书内容（渐进式：默认返回首部+事实+理由段）。",
                    "parameters": {"type": "object", "properties": {"case_number": {"type": "string"}}, "required": ["case_number"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_search_by_law",
                    "description": "检索引用某法条的类案。",
                    "parameters": {"type": "object", "properties": {"legal_basis": {"type": "string"}}, "required": ["legal_basis"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_guided",
                    "description": "检索指导性案例。",
                    "parameters": {"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "将中间结果写入工作目录内的 .md/.json 文件。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "提交类案检索报告并结束。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "report": {"type": "string", "description": "完整类案检索报告"},
                            "answer": {"type": "string", "description": "对用户问题的简洁回答"},
                            "citations": {"type": "array", "items": {"type": "object", "properties": {"case_number": {"type": "string"}, "case_name": {"type": "string"}}}},
                            "needs_human": {"type": "boolean", "default": False},
                        },
                        "required": ["report", "answer"],
                    },
                },
            },
        ]

    def build_system(self, query: str, **kwargs) -> str:
        return (
            "你是类案检索 agent。通过裁判文书检索 MCP 查找案例，只能依据工具返回内容作答，不得编造案例。\n"
            "检索原则：\n"
            "1) 先 case_search 查元数据（只返回总数+前若干条摘要，不读全文）；\n"
            "2) 总数 > 200 时，先收窄条件（案由/法院/日期/关键词）再查；\n"
            "3) 只对最相关的 case_read 读全文，每轮最多读 5 篇；\n"
            "4) 遇到限流/验证码错误时，停止重试并 finish(needs_human=true)；\n"
            "5) 最后用 finish 提交报告。"
        )

    async def execute_tool(self, name: str, args: dict) -> dict:
        timeout = 90 if name in ("case_search", "case_read", "case_search_by_law", "case_guided") else 15
        try:
            if name == "case_search":
                return await asyncio.wait_for(self.search(**args), timeout)
            if name == "case_read":
                return await asyncio.wait_for(self.read(args.get("case_number", "")), timeout)
            if name == "case_search_by_law":
                return await asyncio.wait_for(self.search_by_law(args.get("legal_basis", "")), timeout)
            if name == "case_guided":
                return await asyncio.wait_for(self.guided(args.get("keyword", "")), timeout)
            if name == "write_file":
                return await asyncio.wait_for(asyncio.to_thread(self._write_file, args.get("path", ""), args.get("content", "")), timeout)
            if name == "finish":
                return {"ok": True, **args}
            return {"ok": False, "error": f"未知工具 {name}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"工具超时（>{timeout}s）"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 直接调用方法（供非 ReAct 场景使用） ─────────────────────
    async def search(self, **kwargs) -> dict:
        return await self.adapter.call_tool("search_judgments_tool", kwargs)

    async def read(self, case_number: str) -> dict:
        return await self.adapter.call_tool("get_judgment_by_case_number_tool", {"case_number": case_number})

    async def search_by_law(self, legal_basis: str) -> dict:
        return await self.adapter.call_tool("search_legal_basis_cases_tool", {"legal_basis": legal_basis})

    async def guided(self, keyword: str) -> dict:
        return await self.adapter.call_tool("search_guided_cases_tool", {"keyword": keyword})
