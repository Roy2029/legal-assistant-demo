"""RAG agent（D09 §3）：法律资料检索 ReAct 子代理。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from online_core.agents.base import BaseReActAgent
from online_core.retrieval_service import RetrievalService, get_retrieval_service
from online_core.search_orchestrator import orchestrate

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RAGAgent(BaseReActAgent):
    """知识库检索 agent：kb_index / kb_search / read_file / write_file / finish。"""

    def __init__(self, llm=None, retrieval_service: Optional[RetrievalService] = None, session_id: str = "default", **kwargs):
        super().__init__(llm=llm, session_id=session_id, **kwargs)
        self.svc = retrieval_service or get_retrieval_service()

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
                    "description": "读取工作目录内的 .md/.json 文件。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
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

    def build_system(self, query: str, folders: Optional[list[str]] = None, **kwargs) -> str:
        kb = self._kb_index()
        kb_desc = json.dumps(kb, ensure_ascii=False, indent=2)
        return (
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

    async def execute_tool(self, name: str, args: dict) -> dict:
        timeout = {"kb_search": 90, "kb_index": 10, "read_file": 15, "write_file": 15, "finish": 10}.get(name, 15)
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
