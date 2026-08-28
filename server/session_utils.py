"""会话与消息持久化辅助（供 chat/assistant/agent API 共用）。"""
from __future__ import annotations

import uuid

from .db import get_engine
import sqlalchemy as sa


def ensure_session(session_id: str | None, mode: str = "chat", action: str = "", title: str = "新会话") -> str:
    if not session_id:
        session_id = uuid.uuid4().hex
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT 1 FROM sessions WHERE session_id=:s"), {"s": session_id}).fetchone()
        if not row:
            conn.execute(
                sa.text("INSERT INTO sessions (session_id, mode, action, title) VALUES (:s, :m, :a, :t)"),
                {"s": session_id, "m": mode, "a": action, "t": title},
            )
    engine.dispose()
    return session_id


def append_message(session_id: str, role: str, content: str, msg_kind: str = "final") -> None:
    if not content:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO messages (session_id, role, msg_kind, content) VALUES (:s, :r, :k, :c)"),
            {"s": session_id, "r": role, "k": msg_kind, "c": content[:20000]},
        )
    engine.dispose()
