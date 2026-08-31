"""FastAPI 应用入口（M0 W1 骨架）。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audit import audit_middleware
from .db import init_db
from .errors import register_error_handlers
from .config_service import config_service
from .chat_api import router as chat_router
from .lexicon_api import router as lexicon_router
from .kb_api import router as kb_router
from .assistant_api import router as assistant_router
from .update_api import router as update_router
from .session_api import router as session_router
from .chunk_api import router as chunk_router
from .badcase_api import router as badcase_router
from .agent_api import router as agent_router
from .contract_api import router as contract_router

app = FastAPI(title="法律助手 Demo", version="0.1.0")

# 本地 demo：仅放行 127.0.0.1/localhost 任意端口（dev vite / prod uvicorn）。
# 之前用 allow_origins=["http://127.0.0.1:*"] 是无效的——Starlette 只做精确匹配，
# 端口通配永不命中；改用正则真正放行本地源并阻断其他来源。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(audit_middleware)
app.include_router(chat_router)
app.include_router(lexicon_router)
app.include_router(kb_router)
app.include_router(assistant_router)
app.include_router(update_router)
app.include_router(session_router)
app.include_router(chunk_router)
app.include_router(badcase_router)
app.include_router(agent_router)
app.include_router(contract_router)
register_error_handlers(app)


@app.on_event("startup")
def on_startup():
    init_db()
    # 启动即预加载 embedding + Qdrant store + BM25 encoder + 内置法律词典。
    # 避免首次提问才加载模型（迭代需求 #2），也避免各模块各自 new QdrantClient
    # 触发本地嵌入式 Qdrant 的 AlreadyLocked（迭代需求 #3）。
    try:
        from online_core.retrieval_service import RetrievalConfig, configure_retrieval, DEFAULT_EMBEDDING
        PROJECT_ROOT = Path(__file__).resolve().parents[1]
        rc = config_service.load().get("retrieval", {})
        config = RetrievalConfig(
            index_path=str(PROJECT_ROOT / "data" / "indices" / "法律" / "qdrant"),
            embedding_model=rc.get("embedding_model") or DEFAULT_EMBEDDING,
            embedding_device=rc.get("embedding_device") or "cpu",
            reranker_model=rc.get("reranker_model") or "D:/个人/Research/RAG1.0/local_model/bge-reranker-v2-m3",
            reranker_provider=rc.get("reranker_provider") or "skip",
            reranker_api_url=rc.get("reranker_api_url") or "",
            reranker_api_key=rc.get("reranker_api_key") or "",
            reranker_api_model=rc.get("reranker_api_model") or "bge-reranker-v2-m3",
            enable_rerank=bool(rc.get("enable_rerank", False)),
            recall_top_k=int(rc.get("recall_top_k", 50)),
        )
        svc = configure_retrieval(config)
        svc._get_store()  # 预加载 embedding + store（含 BM25 encoder）
        from online_core.lexicon_service import load_builtin_lexicon
        load_builtin_lexicon()
        print(f"[startup] 检索服务预加载完成 embedding={config.embedding_device} reranker={config.reranker_provider}")
    except Exception as e:
        print(f"[startup] 检索服务预加载失败（将在首次查询时重试）: {e}")


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "legal-assistant",
        "llm_configured": bool(config_service.get_llm().get("api_key")),
    }


@app.get("/api/config")
def get_config():
    # 只返回脱敏视图：明文密钥不离开服务端
    return {"ok": True, "data": config_service.redacted()}


@app.put("/api/config")
def put_config(payload: dict):
    config_service.update(payload)
    return {"ok": True, "data": config_service.redacted()}


# ── 生产打包：由 FastAPI 统一服务前端 dist（安装包不依赖 node/vite）──
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="127.0.0.1", port=8000, reload=True)
