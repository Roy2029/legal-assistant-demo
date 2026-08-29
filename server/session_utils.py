"""会话与消息持久化辅助（供 chat/assistant/agent API 共用）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .db import get_engine
import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACE_DIR = PROJECT_ROOT / "data" / "logs" / "traces"


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


def save_session_trace(session_id: str, trace_type: str, trace: dict) -> None:
    """保存一次会话的 trace（retrieval/agent/assistant）。

    - 数据库 session_traces 表保留最新一条（供会话历史回看）；
    - 同时自动落一份 JSON 文件到 data/logs/traces/（供离线分析）。
    """
    if not session_id or not trace:
        return
    data = json.dumps(trace, ensure_ascii=False)[:200000]
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT id FROM session_traces WHERE session_id=:s AND trace_type=:t"),
            {"s": session_id, "t": trace_type},
        ).fetchone()
        if row:
            conn.execute(
                sa.text("UPDATE session_traces SET trace_json=:j, created_at=datetime('now','localtime') WHERE id=:i"),
                {"j": data, "i": row[0]},
            )
        else:
            conn.execute(
                sa.text("INSERT INTO session_traces (session_id, trace_type, trace_json) VALUES (:s, :t, :j)"),
                {"s": session_id, "t": trace_type, "j": data},
            )
    engine.dispose()

    # 自动持久化 JSON 文件（同一会话+类型覆盖为最新）
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        safe_type = trace_type.replace("/", "_").replace("\\", "_")
        safe_sid = session_id[:12]
        file_path = TRACE_DIR / f"{safe_sid}_{safe_type}_trace.json"
        file_path.write_text(
            json.dumps({"session_id": session_id, "trace_type": trace_type, "trace": trace}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_session_traces(session_id: str) -> dict:
    """读取会话的全部 trace，按 trace_type 返回 dict。"""
    import json
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT trace_type, trace_json FROM session_traces WHERE session_id=:s ORDER BY id ASC"),
            {"s": session_id},
        ).fetchall()
    engine.dispose()
    out = {}
    for t, j in rows:
        try:
            out[t] = json.loads(j)
        except Exception:
            out[t] = {}
    return out
