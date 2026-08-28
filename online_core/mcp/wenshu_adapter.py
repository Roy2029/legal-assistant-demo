"""裁判文书检索 MCP 适配器（D09 §9）。

通过 stdio 启动 `D:/个人开发/裁判文书检索MCP` 的 FastMCP server 并调用工具。
MCP 不可用时返回结构化错误，不抛异常。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

MCP_PROJECT = Path("D:/个人开发/裁判文书检索MCP")
MCP_PYTHON = MCP_PROJECT / ".venv" / "Scripts" / "python.exe"


@dataclass
class WenshuMCPConfig:
    project: Path = MCP_PROJECT
    python: Path = MCP_PYTHON
    connect_timeout: float = 30.0
    call_timeout: float = 90.0


class WenshuMCPAdapter:
    """连接并调用裁判文书检索 MCP 工具。"""

    def __init__(self, config: Optional[WenshuMCPConfig] = None):
        self.config = config or WenshuMCPConfig()

    def available(self) -> bool:
        return self.config.project.exists() and self.config.python.exists()

    async def list_tools(self) -> list[dict]:
        """返回 MCP server 的工具列表。"""
        if not self.available():
            return []
        async with self._session() as session:
            tools = await session.list_tools()
            return [{"name": t.name, "description": t.description or ""} for t in tools.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        """调用单个工具，返回结构化结果。失败不抛异常。"""
        if not self.available():
            return {"ok": False, "error": "MCP 项目不可用", "tool": name}
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments or {})
                text_parts = []
                for item in result.content:
                    text = getattr(item, "text", None)
                    if text:
                        text_parts.append(text)
                return {"ok": True, "tool": name, "result": "\n".join(text_parts)}
        except Exception as e:
            return {"ok": False, "error": str(e), "tool": name}

    def _session(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=str(self.config.python),
            args=["-m", "wenshu_mcp"],
            cwd=str(self.config.project),
        )
        return _MCPSession(stdio_client(params), ClientSession)


class _MCPSession:
    def __init__(self, stdio_ctx, session_cls):
        self.stdio_ctx = stdio_ctx
        self.session_cls = session_cls
        self.session = None

    async def __aenter__(self):
        self.read, self.write = await self.stdio_ctx.__aenter__()
        self.session = self.session_cls(self.read, self.write)
        await self.session.__aenter__()
        await self.session.initialize()
        return self.session

    async def __aexit__(self, *args):
        try:
            if self.session is not None:
                await self.session.__aexit__(*args)
        except Exception:
            pass
        try:
            await self.stdio_ctx.__aexit__(*args)
        except Exception:
            pass
