"""用户知识库 API（W4）：上传/列表/删除，文档解析入库，metadata 隔离。"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from .db import get_engine
import sqlalchemy as sa

from offline_core.manifest import compute_doc_id
from offline_core.chunker_v2 import LegalStructureChunker
from offline_core.data_model import Chunk, HeadingBlock, ParagraphBlock, StructuredDocument
from offline_core.embedder import Embedder

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_tokenizer = None


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer
        _tokenizer = AutoTokenizer.from_pretrained("D:/个人/Research/RAG1.0/local_model/bge-base-zh")
    return _tokenizer
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
DEFAULT_KB_ID = "default"
USER_ID = "local"

router = APIRouter(prefix="/api/kb")

SUPPORTED = {".docx", ".md", ".txt", ".pdf"}


def _parse_docx(path: Path) -> StructuredDocument | None:
    from offline_core.docx_parser import LegalDocxParser, DocxParser
    try:
        lp = LegalDocxParser()
        if lp.detect(str(path)):
            return lp.parse(str(path))
    except Exception:
        pass
    try:
        return DocxParser().parse(str(path))
    except Exception:
        return None


def _parse_md_txt(path: Path) -> StructuredDocument | None:
    from offline_core.parser import MarkdownParser
    try:
        return MarkdownParser().parse(str(path))
    except Exception:
        return None


def _parse_pdf(path: Path) -> StructuredDocument | None:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        blocks = []
        order = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(ParagraphBlock(order=order, content=text.strip()))
                order += 1
        if not blocks:
            return None
        return StructuredDocument(doc_id="", source=str(path), blocks=blocks)
    except Exception:
        return None


def _parse_file(path: Path) -> StructuredDocument | None:
    ext = path.suffix.lower()
    if ext == ".docx":
        return _parse_docx(path)
    if ext in (".md", ".txt"):
        return _parse_md_txt(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    return None


def _ensure_folder(conn, kb_id: str) -> None:
    conn.execute(
        sa.text("INSERT OR IGNORE INTO user_kb (kb_id, name) VALUES (:k, :n)"),
        {"k": kb_id, "n": kb_id},
    )


def _folder_exists(kb_id: str) -> bool:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT 1 FROM user_kb WHERE kb_id=:k"), {"k": kb_id}).fetchone()
    engine.dispose()
    return row is not None


def _set_docs_folder(doc_ids: list[str], kb_id: str) -> None:
    """把一批文档迁移到目标文件夹：更新 Qdrant metadata 与 SQLite user_docs.kb_id。"""
    for doc_id in doc_ids:
        chunks = _load_doc_chunks(doc_id)
        if not chunks:
            continue
        for c in chunks:
            meta = dict(c.metadata or {})
            meta["kb_id"] = kb_id
            meta["folder"] = kb_id
            c.metadata = meta
        _rebuild_doc_chunks(doc_id, chunks)
    engine = get_engine()
    with engine.begin() as conn:
        for d in doc_ids:
            conn.execute(sa.text("UPDATE user_docs SET kb_id=:k WHERE doc_id=:d"), {"k": kb_id, "d": d})
    engine.dispose()


def _delete_user_doc(doc_id: str) -> None:
    """删除单篇用户文档的 Qdrant 点、上传文件和 SQLite 记录。"""
    from online_core.retrieval_service import get_retrieval_service
    store = get_retrieval_service()._get_store()
    store.delete_by_doc_id(doc_id)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT file_path FROM user_docs WHERE doc_id=:d"), {"d": doc_id}).fetchone()
        if row:
            Path(row[0]).unlink(missing_ok=True)
        conn.execute(sa.text("DELETE FROM user_docs WHERE doc_id=:d"), {"d": doc_id})
    engine.dispose()


@router.post("/folders")
def create_folder(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": {"code": "empty_name", "message": "文件夹名不能为空"}}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT OR IGNORE INTO user_kb (kb_id, name) VALUES (:k, :n)"), {"k": name, "n": name})
    engine.dispose()
    return {"ok": True, "data": {"kb_id": name, "name": name}}


@router.get("/folders")
def list_folders():
    engine = get_engine()
    with engine.begin() as conn:
        _ensure_folder(conn, DEFAULT_KB_ID)
        rows = conn.execute(sa.text("SELECT kb_id, name FROM user_kb ORDER BY name")).fetchall()
    engine.dispose()
    return {"ok": True, "data": [dict(r._mapping) for r in rows]}


@router.put("/folders/{kb_id}")
def rename_folder(kb_id: str, payload: dict):
    """文件夹改名：等价于创建新文件夹 → 迁移全部文档 → 删除旧文件夹。"""
    if kb_id == DEFAULT_KB_ID:
        return {"ok": False, "error": {"code": "default_folder", "message": "默认文件夹不能改名"}}
    new_name = (payload.get("name") or "").strip()
    if not new_name:
        return {"ok": False, "error": {"code": "empty_name", "message": "文件夹名不能为空"}}
    if new_name == kb_id:
        return {"ok": True, "data": {"kb_id": kb_id, "name": kb_id}}
    if _folder_exists(new_name):
        return {"ok": False, "error": {"code": "folder_exists", "message": "目标文件夹名已存在"}}
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT doc_id FROM user_docs WHERE kb_id=:k"), {"k": kb_id}).fetchall()
    engine.dispose()
    doc_ids = [r[0] for r in rows]
    if doc_ids:
        _set_docs_folder(doc_ids, new_name)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT OR IGNORE INTO user_kb (kb_id, name) VALUES (:k, :n)"), {"k": new_name, "n": new_name})
        conn.execute(sa.text("DELETE FROM user_kb WHERE kb_id=:old"), {"old": kb_id})
    engine.dispose()
    return {"ok": True, "data": {"kb_id": new_name, "name": new_name}}


@router.delete("/folders/{kb_id}")
def delete_folder(kb_id: str, cascade: bool = False):
    if kb_id == DEFAULT_KB_ID:
        return {"ok": False, "error": {"code": "default_folder", "message": "不能删除默认文件夹"}}
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT doc_id FROM user_docs WHERE kb_id=:k"), {"k": kb_id}).fetchall()
    engine.dispose()
    doc_ids = [r[0] for r in rows]
    if doc_ids and not cascade:
        return {"ok": False, "error": {"code": "folder_not_empty", "message": "文件夹内还有文档，请先移走或使用 cascade=true 级联删除"}}
    for doc_id in doc_ids:
        _delete_user_doc(doc_id)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM user_kb WHERE kb_id=:k"), {"k": kb_id})
    engine.dispose()
    return {"ok": True}


@router.post("/rebuild")
def rebuild_kb():
    """手动重建法律库索引：释放 Qdrant 锁后启动后台重建脚本。"""
    import subprocess
    import sys as _sys

    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    svc.close()  # 释放嵌入式 Qdrant 文件锁，否则重建脚本无法写入

    subprocess.Popen(
        [_sys.executable, str(PROJECT_ROOT / "scripts" / "run_rebuild_managed.py")],
        cwd=str(PROJECT_ROOT),
    )
    return {"ok": True, "data": {"started": True, "message": "重建已启动，请稍后在状态接口查询进度"}}


@router.get("/rebuild/status")
def rebuild_kb_status():
    import json as _json
    status_path = PROJECT_ROOT / "data" / "logs" / "rebuild.status.json"
    if not status_path.exists():
        return {"ok": True, "data": {"running": False, "last": None}}
    try:
        data = _json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        data = {"running": False, "error": "status parse error"}
    return {"ok": True, "data": data}


@router.post("/upload")
async def upload(file: UploadFile = File(...), kb_id: str = Form(DEFAULT_KB_ID)):
    filename = file.filename or "untitled"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        return {"ok": False, "error": {"code": "unsupported_type", "message": f"不支持 {ext}，仅支持 docx/md/txt/pdf"}}
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4().hex
    # 磁盘文件名 = 文档ID + 原文件名，既避免重名覆盖，又保留可读性
    safe_name = Path(filename).name
    save_path = UPLOAD_DIR / f"{doc_id}__{safe_name}"
    content = await file.read()
    save_path.write_bytes(content)

    doc = _parse_file(save_path)
    if doc is None:
        save_path.unlink(missing_ok=True)
        return {"ok": False, "error": {"code": "parse_failed", "message": f"{filename} 解析失败，请尝试转存为 docx/md"}}

    doc.doc_id = compute_doc_id(save_path.read_bytes())
    doc.source = filename

    # 复用检索服务的 embedding 与 store（共享 Qdrant 锁）
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    emb = svc._get_embedding()
    store = svc._get_store()

    chunker = LegalStructureChunker(tokenizer=_get_tokenizer())
    meta = {
        "corpus": "user",
        "user_id": USER_ID,
        "kb_id": kb_id,
        "folder": kb_id,
        "law_name": filename,
        "doc_type": "user",
    }
    parents, children = chunker.chunk(doc, metadata_extra=meta)

    embedder = Embedder(model=emb, cache=None, batch_size=16)
    # 与公共库索引保持一致：只把 child 作为检索单元入库，parent 不占检索点
    records = embedder.embed_chunks(children)
    store.upsert(records)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))

    # SQLite 记录
    engine = get_engine()
    with engine.begin() as conn:
        _ensure_folder(conn, kb_id)
        conn.execute(
            sa.text(
                "INSERT OR REPLACE INTO user_docs (doc_id, kb_id, file_path, parse_status, chunk_count) "
                "VALUES (:d, :k, :f, 'done', :c)"
            ),
            {"d": doc.doc_id, "k": kb_id, "f": str(save_path), "c": len(children)},
        )
    engine.dispose()

    return {"ok": True, "data": {"doc_id": doc.doc_id, "name": filename, "parents": len(parents), "children": len(children)}}


@router.get("/docs")
def list_docs(folder: str | None = None):
    try:
        engine = get_engine()
        sql = "SELECT doc_id, kb_id, file_path, parse_status, chunk_count, created_at FROM user_docs"
        params = {}
        if folder:
            sql += " WHERE kb_id=:f"
            params["f"] = folder
        sql += " ORDER BY created_at DESC"
        with engine.begin() as conn:
            rows = conn.execute(sa.text(sql), params).fetchall()
        engine.dispose()
        return {"ok": True, "data": [dict(r._mapping) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": {"code": "db_error", "message": str(e)}}


@router.put("/docs/{doc_id}/folder")
def move_doc_to_folder(doc_id: str, payload: dict):
    """把单篇文档移动到指定文件夹。"""
    kb_id = (payload.get("kb_id") or "").strip()
    if not kb_id:
        return {"ok": False, "error": {"code": "empty_folder", "message": "文件夹不能为空"}}
    if not _folder_exists(kb_id):
        return {"ok": False, "error": {"code": "folder_not_found", "message": "文件夹不存在，请先创建"}}
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT 1 FROM user_docs WHERE doc_id=:d"), {"d": doc_id}).fetchone()
    engine.dispose()
    if not row:
        return {"ok": False, "error": {"code": "not_found", "message": "文档不存在"}}
    _set_docs_folder([doc_id], kb_id)
    return {"ok": True}


@router.post("/docs/move")
def move_docs_to_folder(payload: dict):
    """批量把文档移动到指定文件夹。"""
    doc_ids = payload.get("doc_ids") or []
    kb_id = (payload.get("kb_id") or "").strip()
    if not doc_ids:
        return {"ok": False, "error": {"code": "empty_docs", "message": "请选择文档"}}
    if not kb_id:
        return {"ok": False, "error": {"code": "empty_folder", "message": "文件夹不能为空"}}
    if not _folder_exists(kb_id):
        return {"ok": False, "error": {"code": "folder_not_found", "message": "文件夹不存在，请先创建"}}
    _set_docs_folder([str(d) for d in doc_ids], kb_id)
    return {"ok": True, "data": {"moved": len(doc_ids)}}


@router.get("/docs/{doc_id}/chunks")
def doc_chunks(doc_id: str):
    """查看某文档在 Qdrant 中的切分结果（只读）。"""
    from online_core.retrieval_service import get_retrieval_service
    try:
        svc = get_retrieval_service()
        store = svc._get_store()
        chunks, _ = store.scroll_paginated(
            filter_condition={"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
            limit=500,
        )
        # Qdrant scroll 默认按内部 point_id 顺序返回；这里按 chunker 写入的 order 排序，
        # 保证与文档原文顺序一致（order 相同的按 chunk_id 稳定兜底）。
        chunks.sort(key=lambda c: (c.order or 0, c.chunk_id))
        data = []
        for ch in chunks:
            meta = ch.metadata or {}
            data.append({
                "chunk_id": ch.chunk_id,
                "text": ch.text,
                "chunk_level": ch.chunk_level,
                "order": ch.order,
                "token_count": ch.token_count,
                "parent_chunk_id": ch.parent_chunk_id,
                "law_name": meta.get("law_name"),
                "article_no": meta.get("article_no"),
                "articles": meta.get("articles"),
                "folder": meta.get("folder"),
                "kb_id": meta.get("kb_id"),
                "doc_type": meta.get("doc_type"),
                "heading_path": ch.heading_path,
            })
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": {"code": "chunk_list_failed", "message": str(e)}}


def _load_doc_chunks(doc_id: str) -> list[Chunk]:
    """读取某文档全部 child chunk，并按原文顺序（order）排序。"""
    from online_core.retrieval_service import get_retrieval_service
    store = get_retrieval_service()._get_store()
    chunks, _ = store.scroll_paginated(
        filter_condition={"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
        limit=500,
    )
    chunks.sort(key=lambda c: (c.order or 0, c.chunk_id))
    return chunks


def _make_user_chunk(doc_id: str, text: str, meta: dict, heading_path, order: int, parent_chunk_id: str | None = None) -> Chunk:
    cid = "chunk:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    tokenizer = _get_tokenizer()
    try:
        token_count = len(tokenizer.encode(text, add_special_tokens=False))
    except Exception:
        token_count = len(text)
    return Chunk(
        chunk_id=cid,
        doc_id=doc_id,
        text=text,
        metadata=dict(meta),
        block_ids=[],
        heading_path=list(heading_path or []),
        order=order,
        token_count=token_count,
        chunk_level="child",
        parent_chunk_id=parent_chunk_id,
        child_chunk_ids=[],
    )


def _upsert_user_chunks(chunks: list[Chunk]) -> None:
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    emb = svc._get_embedding()
    store = svc._get_store()
    embedder = Embedder(model=emb, cache=None, batch_size=16)
    records = embedder.embed_chunks(chunks)
    store.upsert(records)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))


def _rebuild_doc_chunks(doc_id: str, chunks: list[Chunk]) -> None:
    """按给定 chunk 列表重建文档的全部检索点，并同步 SQLite chunk_count。

    先按 doc_id 删除旧点，再重新嵌入；order 在调用方已按原文顺序排好。
    """
    from online_core.retrieval_service import get_retrieval_service
    store = get_retrieval_service()._get_store()
    store.delete_by_doc_id(doc_id)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
    if chunks:
        for i, c in enumerate(chunks):
            c.order = i
        _upsert_user_chunks(chunks)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE user_docs SET chunk_count=:c WHERE doc_id=:id"), {"c": len(chunks), "id": doc_id})
    engine.dispose()


@router.put("/docs/{doc_id}/chunks/{chunk_id}")
def edit_chunk(doc_id: str, chunk_id: str, payload: dict):
    """编辑 chunk 文本：该块按新文本重建（chunk_id 随之变化），其余块保持原顺序。"""
    new_text = (payload.get("text") or "").strip()
    if not new_text:
        return {"ok": False, "error": {"code": "empty_text", "message": "文本不能为空"}}
    chunks = _load_doc_chunks(doc_id)
    hit = None
    for c in chunks:
        if c.chunk_id == chunk_id:
            hit = c
            break
    if hit is None:
        return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
    new_chunk = _make_user_chunk(doc_id, new_text, hit.metadata or {}, hit.heading_path, hit.order, hit.parent_chunk_id)
    new_chunks = [new_chunk if c.chunk_id == chunk_id else c for c in chunks]
    _rebuild_doc_chunks(doc_id, new_chunks)
    return {"ok": True, "data": {"chunk_id": new_chunk.chunk_id}}


@router.post("/docs/{doc_id}/chunks/{chunk_id}/split")
def split_chunk(doc_id: str, chunk_id: str, payload: dict):
    """拆分 chunk 为两个新块，并按原文顺序重建整篇文档的 order。"""
    part1 = (payload.get("part1") or "").strip()
    part2 = (payload.get("part2") or "").strip()
    if not part1 or not part2:
        return {"ok": False, "error": {"code": "empty_parts", "message": "两段文本都不能为空"}}
    chunks = _load_doc_chunks(doc_id)
    idx = next((i for i, c in enumerate(chunks) if c.chunk_id == chunk_id), None)
    if idx is None:
        return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
    old = chunks[idx]
    meta = old.metadata or {}
    c1 = _make_user_chunk(doc_id, part1, meta, old.heading_path, idx, old.parent_chunk_id)
    c2 = _make_user_chunk(doc_id, part2, meta, old.heading_path, idx + 1, old.parent_chunk_id)
    new_chunks = chunks[:idx] + [c1, c2] + chunks[idx + 1:]
    _rebuild_doc_chunks(doc_id, new_chunks)
    return {"ok": True, "data": {"chunks": [c1.chunk_id, c2.chunk_id]}}


@router.post("/docs/{doc_id}/chunks/merge")
def merge_chunks(doc_id: str, payload: dict):
    """合并同文档内两个 chunk（按当前展示顺序拼接），并重建整篇文档 order。"""
    id1 = (payload.get("chunk_id1") or "").strip()
    id2 = (payload.get("chunk_id2") or "").strip()
    if not id1 or not id2 or id1 == id2:
        return {"ok": False, "error": {"code": "bad_ids", "message": "请选择两个不同的 chunk"}}
    chunks = _load_doc_chunks(doc_id)
    i1 = next((i for i, c in enumerate(chunks) if c.chunk_id == id1), None)
    i2 = next((i for i, c in enumerate(chunks) if c.chunk_id == id2), None)
    if i1 is None or i2 is None:
        return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
    if i1 > i2:
        i1, i2 = i2, i1
    c1, c2 = chunks[i1], chunks[i2]
    merged_text = (c1.text or "").rstrip() + "\n\n" + (c2.text or "").lstrip()
    new_chunk = _make_user_chunk(doc_id, merged_text, c1.metadata or {}, c1.heading_path, i1, c1.parent_chunk_id)
    new_chunks = chunks[:i1] + [new_chunk] + chunks[i1 + 1:i2] + chunks[i2 + 1:]
    _rebuild_doc_chunks(doc_id, new_chunks)
    return {"ok": True, "data": {"chunk_id": new_chunk.chunk_id}}


@router.delete("/docs/{doc_id}/chunks/{chunk_id}")
def delete_chunk(doc_id: str, chunk_id: str):
    """删除单个 chunk（文档至少保留 1 个 chunk），并重建整篇文档 order。"""
    chunks = _load_doc_chunks(doc_id)
    if len(chunks) <= 1:
        return {"ok": False, "error": {"code": "last_chunk", "message": "文档至少保留一个分块"}}
    idx = next((i for i, c in enumerate(chunks) if c.chunk_id == chunk_id), None)
    if idx is None:
        return {"ok": False, "error": {"code": "not_found", "message": "chunk 不存在"}}
    new_chunks = chunks[:idx] + chunks[idx + 1:]
    _rebuild_doc_chunks(doc_id, new_chunks)
    return {"ok": True}


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str):
    _delete_user_doc(doc_id)
    return {"ok": True}
