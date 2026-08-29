"""导出会话 trace 到 data/logs/traces/*.json，便于离线分析。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.db import get_engine
import sqlalchemy as sa


def main():
    out_dir = ROOT / "data" / "logs" / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT id, session_id, trace_type, trace_json, created_at FROM session_traces ORDER BY id ASC"
        )).fetchall()
    engine.dispose()

    if not rows:
        print("没有 session_traces 记录。先在「法律助手」中运行一次问答/智能分析后，再执行本脚本。")
        return

    for row in rows:
        rid, session_id, trace_type, trace_json, created_at = row
        safe_type = trace_type.replace("/", "_")
        safe_sid = session_id[:12]
        name = f"{safe_sid}_{safe_type}_trace_{rid}.json"
        path = out_dir / name
        try:
            data = json.loads(trace_json)
        except Exception:
            data = {"raw": trace_json}
        path.write_text(json.dumps({
            "id": rid,
            "session_id": session_id,
            "trace_type": trace_type,
            "created_at": created_at,
            "trace": data,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("导出:", path)

    print("完成，共导出", len(rows), "个 trace 文件 ->", out_dir)


if __name__ == "__main__":
    main()
