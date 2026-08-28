"""RAG agent（D09 §3）：ReAct 循环 + 检索编排工具。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

from online_core.retrieval_service import RetrievalService, get_retrieval_service
from online_core.search_orchestrator import orchestrate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT / "data" / "agent_workspace"

MAX_ITERATIONS = 10
LOOP_TIMEOUT = 180.0
CONTEXT_MAX_TOKENS = 200_000
TOOL_TIMEOUTS = {"kb_search": 90, "kb_index": 10, "read_file": 15, "write_file": 15, "finish": 10}
CONCURRENCY_LIMIT = 2


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _now_ms() -> int:
    return int(time.time() * 1000)


class RAGAgent:
    """法律资料检索 agent（knowledge_agent 内部实现）。"""

    def __init__(
        self,
        llm=None,
        retrieval_service: Optional[RetrievalService] = None,
        session_id: str = "default",
        max_iterations: int = MAX_ITERATIONS,
        loop_timeout: float = LOOP_TIMEOUT,
        context_max_tokens: int = CONTEXT_MAX_TOKENS,
    ):
        from server.llm import llm_client
        self.llm = llm or llm_client
        self.svc = retrieval_service or get_retrieval_service()
        self.session_id = session_id
        self.max_iterations = max_iterations
        self.loop_timeout = loop_timeout
        self.context_max_tokens = context_max_tokens
        self.workspace = WORKSPACE_ROOT / session_id
        self.trace: dict[str, Any] = {"rounds": [], "errors": []}

    # ── 工具 Schema ──────────────────────────────────────────────
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "kb_index",
                    "description": "返回当前知识库概览：公共法律库统计、用户文件夹列表与文档/chunk 数。用于决定该去哪个知识库查。",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "kb_search",
                    "description": "执行一组检索计划。每组可含多个子查询；同一问题多角度分解用 merge_mode=fuse 融合，对比/总结类子查询用 merge_mode=separate 分别保留。folders 空=全部，__public__=公共库，其他=用户文件夹。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "groups": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "group_id": {"type": "string"},
                                        "merge_mode": {"type": "string", "enum": ["fuse", "separate"]},
                                        "queries": {"type": "array", "items": {"type": "string"}},
                                        "folders": {"type": "array", "items": {"type": "string"}},
                                        "top_k": {"type": "integer", "default": 8},
                                    },
                                    "required": ["group_id", "merge_mode", "queries"],
                                },
                            }
                        },
                        "required": ["groups"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取工作目录内的 .md/.json 文件（自动限制在 data/agent_workspace/{session_id}/ 内）。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "将中间结果写入工作目录内的 .md/.json 文件，供后续复用。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "提交资料检索报告并结束。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "report": {"type": "string", "description": "完整检索报告：查询思路/查询过程/回答/引用"},
                            "answer": {"type": "string", "description": "对用户问题的简洁回答"},
                            "citations": {"type": "array", "items": {"type": "object", "properties": {"law_name": {"type": "string"}, "article_no": {"type": "string"}, "chunk_id": {"type": "string"}}}},
                            "needs_human": {"type": "boolean", "default": False},
                        },
                        "required": ["report", "answer"],
                    },
                },
            },
        ]

    # ── 知识库概览 ──────────────────────────────────────────────
    def _kb_index(self) -> dict:
        public_chunks = 17598
        manifest = PROJECT_ROOT / "data" / "indices" / "法律" / "manifest.json"
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text(encoding="utf-8"))
                public_chunks = int(m.get("n_chunks", public_chunks))
            except Exception:
                pass

        from server.db import get_engine
        import sqlalchemy as sa
        folders = []
        try:
            engine = get_engine()
            with engine.begin() as conn:
                rows = conn.execute(sa.text(
                    "SELECT d.kb_id, COUNT(*) AS docs, COALESCE(SUM(d.chunk_count),0) AS chunks "
                    "FROM user_docs d GROUP BY d.kb_id ORDER BY d.kb_id"
                )).fetchall()
            engine.dispose()
            for r in rows:
                folders.append({"folder": r[0], "docs": int(r[1]), "chunks": int(r[2])})
        except Exception:
            pass
        return {
            "public": {"name": "公共法律库", "docs": 471, "chunks": public_chunks, "doc_types": ["law"]},
            "user_folders": folders,
            "note": "用户文件夹按 metadata.folder 过滤；公共库用 __public__ 表示",
        }

    # ── 工作目录读写 ────────────────────────────────────────────
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

    # ── 工具执行 ────────────────────────────────────────────────
    async def _execute_tool(self, name: str, args: dict) -> dict:
        timeout = TOOL_TIMEOUTS.get(name, 15)
        try:
            if name == "kb_index":
                return await asyncio.wait_for(asyncio.to_thread(self._kb_index), timeout)
            if name == "kb_search":
                groups = args.get("groups") or []
                return await asyncio.wait_for(asyncio.to_thread(orchestrate, groups, self.svc), timeout)
            if name == "read_file":
                return await asyncio.wait_for(asyncio.to_thread(self._read_file, args.get("path", "")), timeout)
            if name == "write_file":
                return await asyncio.wait_for(asyncio.to_thread(self._write_file, args.get("path", ""), args.get("content", "")), timeout)
            if name == "finish":
                return {"ok": True, **args}
            return {"ok": False, "error": f"未知工具 {name}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"工具超时（>{timeout}s）"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    # ── 消息构建 ────────────────────────────────────────────────
    def _build_messages(self, query: str, folders: Optional[list[str]]) -> list[dict]:
        kb = self._kb_index()
        kb_desc = json.dumps(kb, ensure_ascii=False, indent=2)
        system = (
            "你是法律资料检索 agent。只能依据检索结果作答，不得编造法条或案例。\n"
            "当前知识库概览：\n" + kb_desc + "\n"
            "检索原则：\n"
            "1) 复杂问题拆成多组子查询，用 kb_search 并行查；\n"
            "2) 同一问题的多角度分解用 merge_mode=fuse，对比/总结类用 separate；\n"
            "3) 单次检索不足时：查询回退（去修饰词）、查询具体化（补法条号/案由）、多角度分解；\n"
            "4) 重要中间结果用 write_file 落盘；\n"
            "5) 最后用 finish 提交报告；引用必须来自检索结果。\n"
            f"用户指定的知识库范围：{json.dumps(folders or [], ensure_ascii=False)}"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]

    # ── 主循环 ──────────────────────────────────────────────────
    async def run(self, query: str, folders: Optional[list[str]] = None) -> dict:
        messages = self._build_messages(query, folders)
        final: dict = {}
        last_content = ""
        consecutive_failures = 0
        start = time.time()

        for iteration in range(1, self.max_iterations + 1):
            if time.time() - start > self.loop_timeout:
                self.trace["errors"].append({"type": "loop_timeout", "iteration": iteration})
                final = {"report": last_content or "检索超时", "answer": last_content or "检索未完成", "needs_human": True, "citations": []}
                break

            messages = self._compress_messages(messages)
            round_trace = {"iteration": iteration, "think_present": False, "tool_calls": [], "elapsed_ms": 0}
            t0 = _now_ms()
            try:
                resp = await self.llm.chat_with_tools(messages, self.tools())
            except Exception as e:
                self.trace["errors"].append({"type": "llm_error", "iteration": iteration, "error": str(e)})
                final = {"report": f"LLM 调用失败：{e}", "answer": "", "needs_human": True, "citations": []}
                break

            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []
            round_trace["think_present"] = bool(content)
            if content:
                last_content = content

            if not tool_calls:
                final = {
                    "report": content or "（模型未给出结束说明）",
                    "answer": content,
                    "needs_human": False,
                    "citations": [],
                }
                break

            if iteration == 1 and not content:
                messages.append({"role": "system", "content": "[system-reminder] 请先用一句话说明你的检索规划，再调用工具。"})

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

            read_only = {tc["name"] in ("kb_index", "kb_search", "read_file") for tc in executed_calls}

            async def run_one(tc):
                t0t = _now_ms()
                result = await self._execute_tool(tc["name"], tc.get("arguments") or {})
                return tc, result, _now_ms() - t0t

            if all(read_only) and len(executed_calls) == 2:
                results = await asyncio.gather(*(run_one(tc) for tc in executed_calls))
            else:
                results = [await run_one(tc) for tc in executed_calls]

            round_failures = 0
            for tc, result, elapsed in results:
                ok = result.get("ok", False) if isinstance(result, dict) else False
                round_trace["tool_calls"].append({"tool": tc["name"], "ok": ok, "elapsed_ms": elapsed, "summary": str(result)[:200]})
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
                messages.append({"role": "system", "content": "[system-reminder] 已连续失败 3 次，请重新评估检索策略后再继续。"})
                consecutive_failures = 0

            round_trace["elapsed_ms"] = _now_ms() - t0
            self.trace["rounds"].append(round_trace)

        if not final:
            final = {
                "report": last_content or "（检索未完成，请人工介入）",
                "answer": last_content or "",
                "needs_human": True,
                "citations": [],
            }

        if "report" not in final or final.get("report") == (last_content or ""):
            final["report"] = (
                f"# 资料检索报告\n\n"
                f"## 查询过程\n共执行 {len(self.trace.get('rounds', []))} 轮工具调用。\n\n"
                f"## 回答\n{final.get('answer', '')}\n\n"
                f"## 引用\n" + json.dumps(final.get("citations", []), ensure_ascii=False)
            )
        return {"ok": True, **final, "trace": self.trace}
