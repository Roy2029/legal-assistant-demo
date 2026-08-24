"""审计日志中间件（SPEC §4.2）。"""
import time
import uuid
from fastapi import Request

from .db import get_engine
import sqlalchemy as sa

async def audit_middleware(request: Request, call_next):
    trace_id = uuid.uuid4().hex
    request.state.trace_id = trace_id
    start = time.time()
    response = await call_next(request)
    elapsed_ms = int((time.time() - start) * 1000)
    # 仅记录 API 请求基本审计
    if request.url.path.startswith("/api"):
        try:
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO audit_logs (session_id, mode, input_masked, output_summary, trace_id) "
                        "VALUES (:s, :m, :i, :o, :t)"
                    ),
                    {
                        "s": request.query_params.get("session_id", ""),
                        "m": request.url.path,
                        "i": f"{request.method} {request.url.path}",
                        "o": f"status={response.status_code} ms={elapsed_ms}",
                        "t": trace_id,
                    },
                )
            engine.dispose()
        except Exception:
            pass
    return response
