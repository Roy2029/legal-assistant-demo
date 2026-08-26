"""chunk 定位 API：引用卡片点击后定位原文。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from online_core.query_parser import LAW_ALIASES

router = APIRouter(prefix="/api/chunk")


@router.get("/{chunk_id}")
def get_chunk(chunk_id: str):
    """按 chunk_id 取完整 chunk（含元信息）。"""
    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
    try:
        # chunk_id 不是 Qdrant point id；用 payload 过滤 scroll
        from qdrant_client import models
        f = models.Filter(must=[models.FieldCondition(key="chunk_id", match=models.MatchValue(value=chunk_id))])
        pts, _ = client.scroll(collection_name="chunks", scroll_filter=f, limit=1, with_payload=True, with_vectors=False)
        if not pts:
            return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
        pl = pts[0].payload or {}
        meta = pl.get("metadata", {})
        return {"ok": True, "data": {
            "chunk_id": pl.get("chunk_id"),
            "text": pl.get("text"),
            "chunk_level": pl.get("chunk_level"),
            "parent_chunk_id": pl.get("parent_chunk_id"),
            "law_name": meta.get("law_name"),
            "article_no": meta.get("article_no"),
            "articles": meta.get("articles"),
            "corpus": meta.get("corpus"),
            "doc_type": meta.get("doc_type"),
            "heading_path": pl.get("heading_path"),
        }}
    finally:
        client.close()


@router.get("/locate")
def locate(law_name: str, article_no: str):
    """按法规名+条文号在 Qdrant 中定位第一条 chunk，返回全文。"""
    # 别名转全称
    canonical = LAW_ALIASES.get(law_name, [law_name])[0]
    from qdrant_client import QdrantClient, models
    client = QdrantClient(path=str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
    try:
        f = models.Filter(
            must=[
                models.FieldCondition(key="metadata.law_name", match=models.MatchValue(value=canonical)),
                models.FieldCondition(key="metadata.articles", match=models.MatchAny(any=[article_no])),
            ]
        )
        pts, _ = client.scroll(collection_name="chunks", scroll_filter=f, limit=1, with_payload=True, with_vectors=False)
        if not pts:
            return {"ok": False, "error": {"code": "not_found", "message": "未定位到原文"}}
        pl = pts[0].payload or {}
        return {"ok": True, "data": {"chunk_id": pl.get("chunk_id"), "text": pl.get("text"), "law_name": canonical, "article_no": article_no}}
    finally:
        client.close()
