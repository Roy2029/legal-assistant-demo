"""BaseReActAgent：通用原生 function calling ReAct 循环（D09 §3）。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT / "data" / "agent_workspace"

MAX_ITERATIONS = 10
LOOP_TIMEOUT = 180.0
CONTEXT_MAX_TOKENS = 200_000
TOOL_TIMEOUTS = {"kb_search": 90, "kb_index": 10, "read_file": 15, "write_file": 15, "finish": 10, "case_search": 90, "case_read": 90}
CONCURRENCY_LIMIT = 2


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _now_ms() -> int:
    return int(time.time() * 1000)


class BaseReActAgent:
    """通用 ReAct 运行时。子类实现 tools() / build_system() / execute_tool()。"""

    def __init__(
        self,
        llm=None,
        session_id: str = "default",
        max_iterations: int = MAX_ITERATIONS,
        loop_timeout: float = LOOP_TIMEOUT,
        context_max_tokens: int = CONTEXT_MAX_TOKENS,
    ):
        from server.llm import llm_client
        self.llm = llm or llm_client
        self.session_id = session_id
        self.max_iterations = max_iterations
        self.loop_timeout = loop_timeout
        self.context_max_tokens = context_max_tokens
        self.workspace = WORKSPACE_ROOT / session_id
        self.trace: dict[str, Any] = {"rounds": [], "errors": []}

    # ── 子类实现 ────────────────────────────────────────────────
    def tools(self) -> list[dict]:
        raise NotImplementedError

    def build_system(self, query: str, **kwargs) -> str:
        raise NotImplementedError

    async def execute_tool(self, name: str, args: dict) -> dict:
        raise NotImplementedError

    # ── 通用工具 ────────────────────────────────────────────────
    def _safe_path(self, rel: str) -> Path:
        p = (self.workspace / rel).resolve()
        if not str(p).startswith(str(self.workspace.resolve())):
            raise ValueError("路径越界")
        if p.suffix not in (".md", ".json"):
            raise ValueError("仅支持 .md / .json")
        return p

    def _read_file(self, rel: str) -> dict:
        p = self._safe_path(rel)
        if not p.exists():
            return {"ok": False, "error": "文件不存在"}
        text = p.read_text(encoding="utf-8")
        if len(text) > 8000:
            text = text[:8000] + f"\n...[截断，共 {len(text)} 字]"
        return {"ok": True, "path": rel, "content": text}

    def _write_file(self, rel: str, content: str) -> dict:
        p = self._safe_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        if len(content) > 200_000:
            return {"ok": False, "error": "内容超过 200KB 上限"}
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}

    # ── 上下文压缩 ──────────────────────────────────────────────
    def _compress_messages(self, messages: list[dict]) -> list[dict]:
        total = sum(_estimate_tokens(str(m.get("content", ""))) for m in messages)
        if total <= self.context_max_tokens:
            return messages
        keep_recent = messages[-4:]
        keep_head = messages[:2]
        old = messages[2:-4]
        summary = "[已压缩] 更早工具结果摘要：\n" + "\n".join(
            f"- {m.get('role','?')}: {str(m.get('content',''))[:200]}" for m in old[-6:]
        )
        return keep_head + [{"role": "system", "content": summary}] + keep_recent

    # ── 主循环 ──────────────────────────────────────────────────
    async def run(self, query: str, event_cb=None, **kwargs) -> dict:
        async def emit(evt):
            if event_cb is not None:
                try:
                    await event_cb(evt)
                except Exception:
                    pass

        messages = [
            {"role": "system", "content": self.build_system(query, **kwargs)},
            {"role": "user", "content": query},
        ]
        final: dict = {}
        last_content = ""
        consecutive_failures = 0
        start = time.time()

        for iteration in range(1, self.max_iterations + 1):
            if time.time() - start > self.loop_timeout:
                self.trace["errors"].append({"type": "loop_timeout", "iteration": iteration})
                await emit({"type": "agent_retry", "reason": "loop_timeout", "iteration": iteration})
                final = {"report": last_content or "检索超时", "answer": last_content or "检索未完成", "needs_human": True, "citations": []}
                break

            messages = self._compress_messages(messages)
            round_trace = {"iteration": iteration, "think_present": False, "tool_calls": [], "elapsed_ms": 0}
            t0 = _now_ms()
            try:
                resp = await self.llm.chat_with_tools(messages, self.tools())
            except Exception as e:
                self.trace["errors"].append({"type": "llm_error", "iteration": iteration, "error": str(e)})
                await emit({"type": "agent_error", "code": "llm_error", "message": str(e)})
                final = {"report": f"LLM 调用失败：{e}", "answer": "", "needs_human": True, "citations": []}
                break

            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []
            round_trace["think_present"] = bool(content)
            if content:
                last_content = content
                await emit({"type": "agent_think", "text": content})

            if not tool_calls:
                final = {"report": content or "（模型未给出结束说明）", "answer": content, "needs_human": False, "citations": []}
                break

            if iteration == 1 and not content:
                messages.append({"role": "system", "content": "[system-reminder] 请先用一句话说明你的规划，再调用工具。"})

            executed_calls = tool_calls[:CONCURRENCY_LIMIT]
            skipped = tool_calls[CONCURRENCY_LIMIT:]
            if skipped:
                messages.append({"role": "system", "content": f"[system-reminder] 单轮最多执行 {CONCURRENCY_LIMIT} 个工具，已忽略 {len(skipped)} 个调用，请下一轮再调。"})

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{iteration}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for i, tc in enumerate(executed_calls)
                ],
            }
            messages.append(assistant_msg)

            read_only = {tc["name"] in ("kb_index", "kb_search", "read_file", "case_search", "case_read") for tc in executed_calls}

            async def run_one(tc):
                t0t = _now_ms()
                result = await self.execute_tool(tc["name"], tc.get("arguments") or {})
                return tc, result, _now_ms() - t0t

            if all(read_only) and len(executed_calls) == 2:
                results = await asyncio.gather(*(run_one(tc) for tc in executed_calls))
            else:
                results = [await run_one(tc) for tc in executed_calls]

            for tc, result, elapsed in results:
                ok = result.get("ok", False) if isinstance(result, dict) else False
                round_trace["tool_calls"].append({"tool": tc["name"], "ok": ok, "elapsed_ms": elapsed, "summary": str(result)[:200]})
                await emit({"type": "agent_tool_call", "tool": tc["name"], "params": tc.get("arguments") or {}})
                await emit({"type": "agent_tool_result", "tool": tc["name"], "ok": ok, "summary": str(result)[:300]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{iteration}_{len(messages)}",
                    "content": json.dumps(result, ensure_ascii=False)[:12000],
                })
                if tc["name"] == "finish" and ok:
                    final = result
                    break
                if not ok:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
            if final:
                break
            if consecutive_failures >= 3:
                messages.append({"role": "system", "content": "[system-reminder] 已连续失败 3 次，请重新评估策略后再继续。"})
                await emit({"type": "agent_retry", "reason": "consecutive_failures", "iteration": iteration})
                consecutive_failures = 0

            round_trace["elapsed_ms"] = _now_ms() - t0
            self.trace["rounds"].append(round_trace)

        if not final:
            final = {"report": last_content or "（检索未完成，请人工介入）", "answer": last_content or "", "needs_human": True, "citations": []}

        if "report" not in final or final.get("report") == (last_content or ""):
            final["report"] = (
                f"# 资料检索报告\n\n"
                f"## 查询过程\n共执行 {len(self.trace.get('rounds', []))} 轮工具调用。\n\n"
                f"## 回答\n{final.get('answer', '')}\n\n"
                f"## 引用\n" + json.dumps(final.get("citations", []), ensure_ascii=False)
            )
        return {"ok": True, **final, "trace": self.trace}
