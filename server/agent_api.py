"""Tool Agent 直接调用 API（D09）：知识库检索（RAG agent）与类案检索（case agent）。"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .session_utils import append_message, ensure_session, load_history_messages

router = APIRouter(prefix="/api")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _session_history(session_id: str) -> list[dict]:
    """加载会话历史 → 脱敏 → 128K 压缩（D1/D2），供 agent 记忆注入。"""
    from .desensitize import apply_to_text
    from .context_compressor import compress_history

    history = load_history_messages(session_id)
    history = [{"role": m["role"], "content": apply_to_text(m["content"])} for m in history]
    return compress_history(history)


async def _run_rag_agent(query: str, session_id: str, folders: Optional[list[str]]):
    # 脱敏：LLM 可见的 query 入口脱敏（可逆假名化，D07），最终答案输出前还原
    from .desensitize import apply_to_text, restore
    masked_query = apply_to_text(query)
    session_id = ensure_session(session_id, mode="assistant", action="rag", title=query[:20] or "新会话")
    # 会话记忆（D1）：先取历史再落库当轮 user 消息，避免 query 重复进入上下文
    history = _session_history(session_id)
    append_message(session_id, "user", query, msg_kind="user")
    import asyncio
    from online_core.agents.rag_agent import RAGAgent
    agent = RAGAgent(session_id=session_id)
    q: asyncio.Queue = asyncio.Queue()

    async def cb(evt):
        await q.put(evt)

    yield _sse({"type": "session_start", "session_id": session_id, "trace_id": uuid.uuid4().hex})
    yield _sse({"type": "agent_start", "agent": "knowledge", "task": query})
    task = asyncio.create_task(agent.run(masked_query, folders=folders, event_cb=cb, history=history))
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
    answer = restore(result.get("answer", ""))
    report = restore(result.get("report", ""))
    try:
        from .session_utils import save_session_trace
        save_session_trace(session_id, "agent", result.get("trace", {}))
    except Exception:
        pass
    yield _sse({"type": "agent_trace", "agent": "knowledge", "trace": result.get("trace", {})})
    yield _sse({
        "type": "agent_report",
        "agent": "knowledge",
        "answer": answer,
        "report": report,
        "citations": result.get("citations", []),
        "needs_human": result.get("needs_human", False),
    })
    final_content = answer
    if report and report != answer:  # 闲聊直答时 report==answer，避免重复拼接
        final_content += "\n\n---\n" + report
    append_message(session_id, "assistant", final_content, msg_kind="final")
    yield _sse({"type": "final", "answer": answer, "report": report, "citations": result.get("citations", []), "needs_human": result.get("needs_human", False)})
    yield _sse({"type": "done"})


async def _run_case_agent(query: str, session_id: str):
    # 脱敏：query 入口脱敏（D07），最终答案输出前还原
    from .desensitize import apply_to_text, restore
    masked_query = apply_to_text(query)
    session_id = ensure_session(session_id, mode="assistant", action="case", title=query[:20] or "新会话")
    # 会话记忆（D1）：先取历史再落库当轮 user 消息
    history = _session_history(session_id)
    append_message(session_id, "user", query, msg_kind="user")
    import asyncio
    from online_core.agents.case_agent import CaseAgent
    agent = CaseAgent(session_id=session_id)
    q: asyncio.Queue = asyncio.Queue()

    async def cb(evt):
        await q.put(evt)

    yield _sse({"type": "session_start", "session_id": session_id, "trace_id": uuid.uuid4().hex})
    yield _sse({"type": "agent_start", "agent": "case", "task": query})
    task = asyncio.create_task(agent.run(masked_query, event_cb=cb, history=history))
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
    answer = restore(result.get("answer", ""))
    report = restore(result.get("report", ""))
    try:
        from .session_utils import save_session_trace
        save_session_trace(session_id, "agent", result.get("trace", {}))
    except Exception:
        pass
    yield _sse({"type": "agent_trace", "agent": "case", "trace": result.get("trace", {})})
    yield _sse({
        "type": "agent_report",
        "agent": "case",
        "answer": answer,
        "report": report,
        "citations": result.get("citations", []),
        "needs_human": result.get("needs_human", False),
    })
    final_content = answer
    if report and report != answer:  # 闲聊直答时 report==answer，避免重复拼接
        final_content += "\n\n---\n" + report
    append_message(session_id, "assistant", final_content, msg_kind="final")
    yield _sse({"type": "final", "answer": answer, "report": report, "citations": result.get("citations", []), "needs_human": result.get("needs_human", False)})
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
