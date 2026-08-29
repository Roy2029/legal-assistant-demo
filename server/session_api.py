"""会话管理 API（M1 W7）：创建/列表/删除会话，消息历史。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from .db import get_engine
from .session_utils import load_session_traces, save_session_trace
import sqlalchemy as sa

router = APIRouter(prefix="/api/sessions")


@router.post("")
def create_session(payload: dict | None = None):
    session_id = uuid.uuid4().hex
    title = (payload or {}).get("title") or "新会话"
    mode = (payload or {}).get("mode") or "chat"
    action = (payload or {}).get("action") or ""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO sessions (session_id, mode, action, title) VALUES (:s, :m, :a, :t)"), {"s": session_id, "m": mode, "a": action, "t": title})
    engine.dispose()
    return {"ok": True, "data": {"session_id": session_id, "title": title, "mode": mode, "action": action}}


@router.get("")
def list_sessions(mode: str | None = None, action: str | None = None):
    engine = get_engine()
    sql = "SELECT session_id, mode, action, title, created_at FROM sessions"
    params = {}
    conds = []
    if mode:
        conds.append("mode=:m")
        params["m"] = mode
    if action:
        conds.append("action=:a")
        params["a"] = action
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    with engine.begin() as conn:
        rows = conn.execute(sa.text(sql), params).fetchall()
    engine.dispose()
    return {"ok": True, "data": [dict(r._mapping) for r in rows]}


@router.put("/{session_id}")
def rename_session(session_id: str, payload: dict):
    title = (payload or {}).get("title") or ""
    title = title.strip()
    if not title:
        return {"ok": False, "error": {"code": "empty_title", "message": "标题不能为空"}}
    engine = get_engine()
    with engine.begin() as conn:
        cur = conn.execute(sa.text("UPDATE sessions SET title=:t WHERE session_id=:s"), {"t": title, "s": session_id})
        ok = cur.rowcount > 0
    engine.dispose()
    if not ok:
        return {"ok": False, "error": {"code": "not_found", "message": "会话不存在"}}
    return {"ok": True, "data": {"session_id": session_id, "title": title}}


@router.delete("/{session_id}")
def delete_session(session_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM messages WHERE session_id=:s"), {"s": session_id})
        conn.execute(sa.text("DELETE FROM sessions WHERE session_id=:s"), {"s": session_id})
    engine.dispose()
    return {"ok": True}


@router.get("/{session_id}/messages")
def get_messages(session_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT role, msg_kind, content, created_at FROM messages WHERE session_id=:s ORDER BY id ASC"),
            {"s": session_id},
        ).fetchall()
    engine.dispose()
    return {"ok": True, "data": [dict(r._mapping) for r in rows]}


@router.get("/{session_id}/traces")
def get_traces(session_id: str):
    return {"ok": True, "data": load_session_traces(session_id)}


@router.post("/{session_id}/traces")
def save_trace(session_id: str, payload: dict):
    trace_type = (payload.get("trace_type") or "").strip()
    trace = payload.get("trace")
    if not trace_type or not isinstance(trace, dict):
        return {"ok": False, "error": {"code": "bad_trace", "message": "trace_type 与 trace 对象不能为空"}}
    save_session_trace(session_id, trace_type, trace)
    return {"ok": True, "data": {"session_id": session_id, "trace_type": trace_type}}
