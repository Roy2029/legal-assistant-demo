"""统一错误处理。"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": {"code": "internal_error", "message": str(exc)}},
        )
