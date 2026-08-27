"""chunk 定位 API：引用卡片点击后定位原文。

注意：统一走 online_core.retrieval_service 单例共享 QdrantClient。
本地嵌入式 Qdrant 同一存储目录只允许一个进程内一个 client 实例；
直接 new QdrantClient(path) 会触发 AlreadyLocked，影响全文查看 / 实务助手 / 知识库上传。
"""
from __future__ import annotations

from fastapi import APIRouter

from online_core.query_parser import LAW_ALIASES

router = APIRouter(prefix="/api/chunk")


def _get_store():
    from online_core.retrieval_service import get_retrieval_service
    return get_retrieval_service()._get_store()


@router.get("/locate")
def locate(law_name: str, article_no: str):
    """按法规名+条文号在 Qdrant 中定位第一条 chunk，返回全文。"""
    # 别名转全称
    canonical = LAW_ALIASES.get(law_name, [law_name])[0]
    store = _get_store()
    chunks, _ = store.scroll_paginated(
        filter_condition={
            "must": [
                {"key": "metadata.law_name", "match": {"value": canonical}},
                {"key": "metadata.articles", "match": {"any": [article_no]}},
            ]
        },
        limit=1,
    )
    if not chunks:
        return {"ok": False, "error": {"code": "not_found", "message": "未定位到原文"}}
    ch = chunks[0]
    return {"ok": True, "data": {"chunk_id": ch.chunk_id, "text": ch.text, "law_name": canonical, "article_no": article_no}}
@router.get("/{chunk_id}")
def get_chunk(chunk_id: str):
    """按 chunk_id 取完整 chunk（含元信息）。"""
    store = _get_store()
    chunks, _ = store.scroll_paginated(
        filter_condition={"must": [{"key": "chunk_id", "match": {"value": chunk_id}}]},
        limit=1,
    )
    if not chunks:
        return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
    ch = chunks[0]
    meta = ch.metadata or {}
    return {"ok": True, "data": {
        "chunk_id": ch.chunk_id,
        "text": ch.text,
        "chunk_level": ch.chunk_level,
        "parent_chunk_id": ch.parent_chunk_id,
        "law_name": meta.get("law_name"),
        "article_no": meta.get("article_no"),
        "articles": meta.get("articles"),
        "corpus": meta.get("corpus"),
        "doc_type": meta.get("doc_type"),
        "heading_path": ch.heading_path,
    }}


