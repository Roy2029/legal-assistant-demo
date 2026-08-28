"""类案检索 agent（D09 §9 v1）：MCP 接入 + 渐进式读取。

v1 先提供可直接调用的工具方法；ReAct 循环待 BaseReActAgent 抽出后复用。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from online_core.mcp.wenshu_adapter import WenshuMCPAdapter


class CaseAgent:
    """类案检索 agent 工具集。"""

    def __init__(self, adapter: Optional[WenshuMCPAdapter] = None):
        self.adapter = adapter or WenshuMCPAdapter()

    @staticmethod
    def tools() -> list[dict]:
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
        ]

    async def search(self, **kwargs) -> dict:
        return await self.adapter.call_tool("search_judgments_tool", kwargs)

    async def read(self, case_number: str) -> dict:
        return await self.adapter.call_tool("get_judgment_by_case_number_tool", {"case_number": case_number})

    async def search_by_law(self, legal_basis: str) -> dict:
        return await self.adapter.call_tool("search_legal_basis_cases_tool", {"legal_basis": legal_basis})

    async def guided(self, keyword: str) -> dict:
        return await self.adapter.call_tool("search_guided_cases_tool", {"keyword": keyword})

    async def run(self, query: str) -> dict:
        """v1 规则式流程：搜索 → 如结果过多则提示缩小范围 → 读取前 3 篇。"""
        r = await self.search(fulltext_keyword=query, page_num=1, page_size=15)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error"), "query": query}
        result_text = r.get("result", "")
        try:
            result_data = json.loads(result_text)
        except Exception:
            result_data = {"raw": result_text[:2000]}
        return {
            "ok": True,
            "query": query,
            "total": result_data.get("total", 0) if isinstance(result_data, dict) else None,
            "documents": (result_data.get("documents", []) if isinstance(result_data, dict) else [])[:15],
            "note": "如需查看具体文书，请调用 case_read(case_number)。",
        }
