"""wenshu_mcp：裁判文书检索 MCP 工具包（明文编排层）。

真正的协议 / 解密逻辑在 wenshu_api 内核（可 Cython 编译为 .pyd）。
本包只负责：暴露 MCP 工具、包裹会话保活、把异常映射成结构化 error_code。
"""
from . import errors
from .agent_session import AgentSession, get_session
from .server import mcp, main

__all__ = ["mcp", "main", "errors", "AgentSession", "get_session"]
