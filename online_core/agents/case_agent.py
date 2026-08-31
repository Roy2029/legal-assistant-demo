"""类案检索 agent（D09 §9）：ReAct 子代理，接裁判文书检索 MCP（WenshuMCP 2026-08 新版）。

新版 MCP 工具为 search / advanced_search / get_document / batch_download /
session_status / login / reset_cooldown / health_check，本 agent 暴露其中业务子集，
凭据经 WenshuMCPConfig.env 注入子进程（来源 server.config_service，不落盘）。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from online_core.agents.base import BaseReActAgent
from online_core.mcp.wenshu_adapter import WenshuMCPAdapter, WenshuMCPConfig

CASE_TOOLS = ("case_search", "case_read", "case_status", "case_login")


def _wenshu_env() -> dict[str, str]:
    """从加密配置服务读取裁判文书网凭据，注入 MCP 子进程环境。"""
    try:
        from server.config_service import config_service

        w = config_service.get_wenshu()
        return {
            "WENSHU_USER_NAME": w.get("username") or "",
            "WENSHU_PASSWORD": w.get("password") or "",
        }
    except Exception:
        return {}


class CaseAgent(BaseReActAgent):
    """类案检索 agent：case_search / case_read / case_status / case_login。"""

    def __init__(self, adapter: Optional[WenshuMCPAdapter] = None, llm=None, session_id: str = "default", **kwargs):
        super().__init__(llm=llm, session_id=session_id, **kwargs)
        if adapter is None:
            adapter = WenshuMCPAdapter(WenshuMCPConfig(env=_wenshu_env()))
        self.adapter = adapter

    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "case_search",
                    "description": (
                        "多条件检索裁判文书元数据（返回 total 与 items：doc_id/案号/案件名称/法院/案由/"
                        "裁判日期/摘要），不返回全文。keyword 必填；日期区间过滤暂不支持，"
                        "默认按裁判时间降序（sort=s50:desc）近似。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keyword": {"type": "string", "description": "全文关键词，如「建设工程施工合同 实际施工人」或法条名称"},
                            "court": {"type": "string", "description": "法院名称"},
                            "case_type": {"type": "string", "description": "案件类型，如「民事案件」「刑事案件」"},
                            "cause": {"type": "string", "description": "案由"},
                            "trial_procedure": {"type": "string", "description": "审判程序，如「一审」「二审」"},
                            "sort": {"type": "string", "description": "排序，默认 s50:desc（按裁判时间降序）"},
                            "page": {"type": "integer", "default": 1},
                            "page_size": {"type": "integer", "default": 10, "description": "每页条数，站点仅接受 5/10/20"},
                        },
                        "required": ["keyword"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_read",
                    "description": "按 doc_id 读取单篇文书（doc_id 来自 case_search 返回的 items）。format=text 纯文本（默认，省 token）| json 结构化字段。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string"},
                            "format": {"type": "string", "enum": ["text", "json"], "default": "text"},
                        },
                        "required": ["doc_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_status",
                    "description": "查询检索会话健康度（登录态/会话年龄/后端）。检索失败排查时用。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "case_login",
                    "description": (
                        "人工登录裁判文书网：弹出浏览器窗口，由用户点选文字验证码（约 1-2 分钟）。"
                        "仅在检索返回 CAPTCHA_FAILED / SESSION_EXPIRED 后调用一次；正常情况业务工具自动复用会话，无需调用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"force": {"type": "boolean", "description": "忽略会话快照强制重登，默认 False"}},
                    },
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
            "你是类案检索 agent。通过裁判文书检索 MCP 查找中国裁判文书网案例，只能依据工具返回内容作答，不得编造案例。\n"
            "检索原则：\n"
            "1) 先 case_search 查元数据（返回 total + items，含 doc_id/案号/法院/案由/日期/摘要，不含全文）；\n"
            "2) total > 200 或结果不相关时，先收窄条件（案由/法院/案件类型/关键词）再查；日期区间暂不支持，靠默认排序近似；\n"
            "   total=0 时未必是没有类案——可能是站点软拦截或会话失效，先换更宽泛的 keyword 重查一次，仍为 0 则提示用户稍后再试或重新登录；\n"
            "3) 只对最相关的文书 case_read 读全文（doc_id 取自 items），单轮最多 2 个工具调用；\n"
            "4) 按法条找类案时，把法条名称（如「中华人民共和国劳动合同法 第八十五条」）作为 keyword 检索；\n"
            "5) 错误处理（看返回的 error_code）：\n"
            "   - RATE_LIMITED：不要重试，finish(needs_human=true) 并说明触发限流；\n"
            "   - CAPTCHA_FAILED / SESSION_EXPIRED：先向用户说明，再 case_login（弹出浏览器窗口，人工点选文字验证码），成功后重试原查询一次；仍失败则 finish(needs_human=true)；\n"
            "   - NEED_SETUP / 其他连续失败：finish(needs_human=true)；\n"
            "6) 引用必须给出真实案号与案件名称；最后用 finish 提交报告。"
        )

    async def execute_tool(self, name: str, args: dict) -> dict:
        timeouts = {"case_search": 90, "case_read": 90, "case_status": 30, "case_login": 170}
        timeout = timeouts.get(name, 15)
        try:
            if name == "case_search":
                return await asyncio.wait_for(self.search(**args), timeout)
            if name == "case_read":
                return await asyncio.wait_for(self.read(args.get("doc_id", ""), args.get("format", "text")), timeout)
            if name == "case_status":
                return await asyncio.wait_for(self.status(), timeout)
            if name == "case_login":
                return await asyncio.wait_for(
                    self.login(solve_mode=args.get("solve_mode", "human"),
                               human_timeout=args.get("human_timeout", 120),
                               force=bool(args.get("force", False))),
                    timeout,
                )
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
    async def search(self, keyword: str, page: int = 1, page_size: int = 10, **filters) -> dict:
        """多条件检索（透传 court/case_type/cause/trial_procedure/sort）。"""
        return await self.adapter.call_tool("advanced_search", {"keyword": keyword, "page": page, "page_size": page_size, **filters})

    async def read(self, doc_id: str, format: str = "text") -> dict:
        return await self.adapter.call_tool("get_document", {"doc_id": doc_id, "format": format})

    async def status(self) -> dict:
        return await self.adapter.call_tool("session_status", {})

    async def login(self, solve_mode: str = "human", human_timeout: int = 120, force: bool = False) -> dict:
        return await self.adapter.call_tool(
            "login", {"solve_mode": solve_mode, "human_timeout": human_timeout, "force": force},
            timeout=max(180, human_timeout + 60),
        )
