"""badcase 反馈 API（M2 W14 闭环）：前端点踩/点赞落库，开发者查询/改状态/导出。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from .db import get_engine
import sqlalchemy as sa

router = APIRouter(prefix="/api/badcases")

REASONS = {"good", "retrieval", "citation", "off_topic", "format", "other"}


@router.post("")
def create_badcase(payload: dict):
    mode = payload.get("mode") or "chat"
    action = payload.get("action")
    query = (payload.get("query") or "").strip()
    answer = payload.get("answer") or ""
    reason = payload.get("reason") or "other"
    if reason not in REASONS:
        reason = "other"
    note = (payload.get("note") or "").strip()
    trace = payload.get("trace")
    trace_json = json.dumps(trace, ensure_ascii=False) if trace is not None else None

    if not query:
        return {"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}

    engine = get_engine()
    with engine.begin() as conn:
        cur = conn.execute(
            sa.text(
                "INSERT INTO badcase_feedback (session_id, trace_id, mode, action, query, answer, reason, note, trace_json, status) "
                "VALUES (:s, :t, :m, :a, :q, :ans, :r, :n, :tj, 'new')"
            ),
            {
                "s": payload.get("session_id"),
                "t": payload.get("trace_id"),
                "m": mode,
                "a": action,
                "q": query,
                "ans": answer[:4000],
                "r": reason,
                "n": note[:2000],
                "tj": trace_json,
            },
        )
        fid = cur.lastrowid
    engine.dispose()
    return {"ok": True, "data": {"id": fid, "status": "new"}}


@router.get("")
def list_badcases(status: str | None = None, limit: int = 50):
    engine = get_engine()
    sql = "SELECT id, session_id, trace_id, mode, action, query, reason, note, status, created_at FROM badcase_feedback"
    params = {}
    if status:
        sql += " WHERE status=:s"
        params["s"] = status
    sql += " ORDER BY id DESC LIMIT :n"
    params["n"] = min(max(limit, 1), 500)
    with engine.begin() as conn:
        rows = conn.execute(sa.text(sql), params).fetchall()
    engine.dispose()
    return {"ok": True, "data": [dict(r._mapping) for r in rows]}


@router.put("/{badcase_id}")
def update_badcase(badcase_id: int, payload: dict):
    fields = []
    params = {"id": badcase_id}
    if "status" in payload:
        fields.append("status=:status")
        params["status"] = payload["status"]
    if "root_cause" in payload:
        fields.append("root_cause=:root_cause")
        params["root_cause"] = payload["root_cause"]
    if "note" in payload:
        fields.append("note=:note")
        params["note"] = payload["note"]
    if not fields:
        return {"ok": False, "error": {"code": "empty_update", "message": "无可更新字段"}}
    fields.append("updated_at=datetime('now','localtime')")
    engine = get_engine()
    with engine.begin() as conn:
        cur = conn.execute(sa.text(f"UPDATE badcase_feedback SET {', '.join(fields)} WHERE id=:id"), params)
        ok = cur.rowcount > 0
    engine.dispose()
    if not ok:
        return {"ok": False, "error": {"code": "not_found", "message": "记录不存在"}}
    return {"ok": True}


@router.get("/summary")
def summary():
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT reason, status, COUNT(*) AS cnt FROM badcase_feedback GROUP BY reason, status ORDER BY reason, status"
        )).fetchall()
    engine.dispose()
    data = [dict(r._mapping) for r in rows]
    total = sum(r["cnt"] for r in data)
    return {"ok": True, "data": {"total": total, "groups": data}}
