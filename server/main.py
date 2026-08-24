"""FastAPI 应用入口（M0 W1 骨架）。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audit import audit_middleware
from .db import init_db
from .errors import register_error_handlers
from .config_service import config_service
from .chat_api import router as chat_router

app = FastAPI(title="法律助手 Demo", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(audit_middleware)
app.include_router(chat_router)
register_error_handlers(app)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "legal-assistant",
        "llm_configured": bool(config_service.get_llm().get("api_key")),
    }


@app.get("/api/config")
def get_config():
    return {"ok": True, "data": config_service.load()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="127.0.0.1", port=8000, reload=True)
