"""Tool Agent 直接调用 API（D09）：知识库检索（RAG agent）与类案检索（case agent）。"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _run_rag_agent(query: str, session_id: str, folders: Optional[list[str]]):
    import asyncio
    from online_core.agents.rag_agent import RAGAgent
    agent = RAGAgent(session_id=session_id)
    q: asyncio.Queue = asyncio.Queue()

    async def cb(evt):
        await q.put(evt)

    yield _sse({"type": "session_start", "session_id": session_id, "trace_id": uuid.uuid4().hex})
    yield _sse({"type": "agent_start", "agent": "knowledge", "task": query})
    task = asyncio.create_task(agent.run(query, folders=folders, event_cb=cb))
    while not task.done() or not q.empty():
        try:
            evt = await asyncio.wait_for(q.get(), timeout=0.2)
            yield _sse(evt)
        except asyncio.TimeoutError:
            continue
    try:
        result = task.result()
    except Exception as e:
        yield _sse({"type": "error", "code": "rag_agent_failed", "message": str(e)})
        yield _sse({"type": "done"})
        return
    yield _sse({"type": "agent_trace", "agent": "knowledge", "trace": result.get("trace", {})})
    yield _sse({
        "type": "agent_report",
        "agent": "knowledge",
        "answer": result.get("answer", ""),
        "report": result.get("report", ""),
        "citations": result.get("citations", []),
        "needs_human": result.get("needs_human", False),
    })
    yield _sse({"type": "final", "answer": result.get("answer", ""), "report": result.get("report", ""), "citations": result.get("citations", []), "needs_human": result.get("needs_human", False)})
    yield _sse({"type": "done"})


async def _run_case_agent(query: str, session_id: str):
    import asyncio
    from online_core.agents.case_agent import CaseAgent
    agent = CaseAgent(session_id=session_id)
    q: asyncio.Queue = asyncio.Queue()

    async def cb(evt):
        await q.put(evt)

    yield _sse({"type": "session_start", "session_id": session_id, "trace_id": uuid.uuid4().hex})
    yield _sse({"type": "agent_start", "agent": "case", "task": query})
    task = asyncio.create_task(agent.run(query, event_cb=cb))
    while not task.done() or not q.empty():
        try:
            evt = await asyncio.wait_for(q.get(), timeout=0.2)
            yield _sse(evt)
        except asyncio.TimeoutError:
            continue
    try:
        result = task.result()
    except Exception as e:
        yield _sse({"type": "error", "code": "case_agent_failed", "message": str(e)})
        yield _sse({"type": "done"})
        return
    yield _sse({"type": "agent_trace", "agent": "case", "trace": result.get("trace", {})})
    yield _sse({
        "type": "agent_report",
        "agent": "case",
        "answer": result.get("answer", ""),
        "report": result.get("report", ""),
        "citations": result.get("citations", []),
        "needs_human": result.get("needs_human", False),
    })
    yield _sse({"type": "final", "answer": result.get("answer", ""), "report": result.get("report", ""), "citations": result.get("citations", []), "needs_human": result.get("needs_human", False)})
    yield _sse({"type": "done"})


@router.post("/rag-agent")
async def rag_agent(payload: dict):
    query = (payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}
    session_id = payload.get("session_id") or uuid.uuid4().hex
    folders = payload.get("folders")
    if isinstance(folders, str):
        folders = [folders]
    return StreamingResponse(_run_rag_agent(query, session_id, folders), media_type="text/event-stream")


@router.post("/case-agent")
async def case_agent(payload: dict):
    query = (payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}
    session_id = payload.get("session_id") or uuid.uuid4().hex
    return StreamingResponse(_run_case_agent(query, session_id), media_type="text/event-stream")
