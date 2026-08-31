"""裁判文书检索 MCP 适配器（D09 §9）。

通过 stdio 启动 WenshuMCP 的 FastMCP server 并调用工具（项目位置解析见下方 MCP_PROJECT）。
MCP 不可用时返回结构化错误，不抛异常。

新版 MCP（2026-08 迭代）入口为 ``python -m wenshu_mcp.server``，工具返回统一
JSON：``{ok, error_code, data | message}``（见 wenshu_mcp/errors.py）。
适配器优先解析该结构并透传 ``error_code``/``data``，解析失败时退回纯文本 ``result``。

凭据：新版 MCP 从子进程环境读取 ``WENSHU_USER_NAME`` / ``WENSHU_PASSWORD``，
通过 ``WenshuMCPConfig.env`` 注入（不落盘）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# MCP 项目位置解析顺序（首个含 wenshu_mcp/server.py 的生效）：
# 1) WENSHU_MCP_PROJECT 环境变量（换机/多版本调试）
# 2) 仓库/安装目录内 vendor/wenshumcp（随包分发形态，scripts/vendor_wenshumcp.py 维护）
# 3) 开发机 WenshuMCP 工作副本（历史默认路径，兼容本地开发环境）
_MCP_PROJECT_CANDIDATES = (
    _REPO_ROOT / "vendor" / "wenshumcp",
    Path("C:/Users/Roy/WorkBuddy/WenshuMCP"),
)
MCP_PROJECT = Path(
    os.getenv("WENSHU_MCP_PROJECT")
    or next(
        (str(p) for p in _MCP_PROJECT_CANDIDATES if (p / "wenshu_mcp" / "server.py").exists()),
        str(_MCP_PROJECT_CANDIDATES[-1]),
    )
)
# MCP 子进程解释器：默认与应用同环境（打包后即随包解释器），要求该环境具备
# wenshu_mcp 的依赖（mcp / playwright+chromium / ddddocr / pycryptodome / python-docx）。
# 开发机如需指定专用解释器，设 WENSHU_MCP_PYTHON 即可。
MCP_PYTHON = Path(os.getenv("WENSHU_MCP_PYTHON", sys.executable))

# 随包 vendor 版本信息（vendor/wenshumcp/VENDOR.json），用于排障追溯
try:
    _VENDOR_META = json.loads(
        (_REPO_ROOT / "vendor" / "wenshumcp" / "VENDOR.json").read_text(encoding="utf-8")
    )
    VENDOR_INFO: Optional[dict] = {
        "vendored_at": _VENDOR_META.get("vendored_at"),
        "fingerprint": (_VENDOR_META.get("content_fingerprint") or "")[:12],
    }
except Exception:
    VENDOR_INFO = None


@dataclass
class WenshuMCPConfig:
    project: Path = MCP_PROJECT
    python: Path = MCP_PYTHON
    env: dict[str, str] = field(default_factory=dict)  # 注入 MCP 子进程的环境变量
    connect_timeout: float = 30.0
    call_timeout: float = 90.0


class WenshuMCPAdapter:
    """连接并调用裁判文书检索 MCP 工具。

    超时策略：每次调用在独立 task 里完成「连接→调用→关闭」，超时只放弃等待而不
    取消该 task——mcp 1.x 的会话/anyio 任务组不支持从外部取消（会崩 cancel
    scope），放任其在后台自然收尾（会话正常关闭、子进程随后退出）。
    """

    def __init__(self, config: Optional[WenshuMCPConfig] = None):
        self.config = config or WenshuMCPConfig()
        self._inflight: set[asyncio.Task] = set()
        logger.info(
            "WenshuMCP 适配器初始化：project=%s python=%s vendor=%s",
            self.config.project, self.config.python, VENDOR_INFO or "无随包信息",
        )

    def available(self) -> bool:
        return (
            self.config.project.exists()
            and (self.config.project / "wenshu_mcp" / "server.py").exists()
            and self.config.python.exists()
        )

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._inflight.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)
        if not task.cancelled() and task.exception() is not None:
            task.exception()  # 取回异常，避免「never retrieved」告警

    @staticmethod
    async def _wait(task: asyncio.Task, timeout: float) -> bool:
        """等待 task 完成；超时返回 False（task 留在后台自然收尾）。"""
        done, _ = await asyncio.wait({task}, timeout=timeout)
        return task in done

    async def list_tools(self, timeout: float | None = None) -> list[dict]:
        """返回 MCP server 的工具列表。"""
        if not self.available():
            return []
        timeout = timeout or self.config.connect_timeout

        async def _run() -> list[dict]:
            async with self._session() as session:
                tools = await session.list_tools()
                return [{"name": t.name, "description": t.description or ""} for t in tools.tools]

        try:
            task = self._spawn(_run())
            if await self._wait(task, timeout):
                return task.result()
        except Exception:
            return []
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> dict:
        """调用单个工具，返回结构化结果。失败不抛异常，默认 call_timeout 秒超时。"""
        if not self.available():
            return {"ok": False, "error_code": "NEED_SETUP", "error": "MCP 项目不可用", "tool": name}
        timeout = timeout or self.config.call_timeout

        async def _run() -> dict:
            async with self._session() as session:
                result = await session.call_tool(name, arguments or {})
                text = "\n".join(
                    getattr(item, "text", "") or "" for item in result.content
                ).strip()
                return self._parse_result(name, text)

        try:
            task = self._spawn(_run())
            if await self._wait(task, timeout):
                return task.result()
        except Exception as e:
            return {"ok": False, "error_code": "MCP_ERROR", "error": str(e), "tool": name}
        return {"ok": False, "error_code": "TIMEOUT", "error": f"MCP 调用超时（>{timeout}s）", "tool": name}

    @staticmethod
    def _parse_result(name: str, text: str) -> dict:
        """解析 MCP 统一 JSON 返回；非 JSON 文本退回 ``result`` 字段。"""
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and "ok" in payload:
            return {"tool": name, **payload, "result": text}
        return {"ok": True, "tool": name, "result": text}

    def _session(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client

        env = get_default_environment()
        env.update({k: v for k, v in self.config.env.items() if v})
        params = StdioServerParameters(
            command=str(self.config.python),
            args=["-m", "wenshu_mcp.server"],
            cwd=str(self.config.project),
            env=env,
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
