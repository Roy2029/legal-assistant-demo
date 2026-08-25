"""chunk 定位 API：引用卡片点击后定位原文。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from online_core.query_parser import LAW_ALIASES

router = APIRouter(prefix="/api/chunk")


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
