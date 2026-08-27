"""用户知识库 API（W4）：上传/列表/删除，文档解析入库，metadata 隔离。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from .db import get_engine
import sqlalchemy as sa

from offline_core.manifest import compute_doc_id
from offline_core.chunker_v2 import LegalStructureChunker
from offline_core.data_model import HeadingBlock, ParagraphBlock, StructuredDocument
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


@router.post("/upload")
async def upload(file: UploadFile = File(...), kb_id: str = DEFAULT_KB_ID):
    filename = file.filename or "untitled"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        return {"ok": False, "error": {"code": "unsupported_type", "message": f"不支持 {ext}，仅支持 docx/md/txt/pdf"}}
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4().hex
    save_path = UPLOAD_DIR / f"{doc_id}{ext}"
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
        "law_name": filename,
        "doc_type": "user",
    }
    parents, children = chunker.chunk(doc, metadata_extra=meta)

    embedder = Embedder(model=emb, cache=None, batch_size=16)
    records = embedder.embed_chunks(parents + children)
    store.upsert(records)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))

    # SQLite 记录
    engine = get_engine()
    with engine.begin() as conn:
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
def list_docs():
    try:
        engine = get_engine()
        with engine.begin() as conn:
            rows = conn.execute(sa.text("SELECT doc_id, kb_id, file_path, parse_status, chunk_count, created_at FROM user_docs ORDER BY created_at DESC")).fetchall()
        engine.dispose()
        return {"ok": True, "data": [dict(r._mapping) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": {"code": "db_error", "message": str(e)}}


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str):
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    store = svc._get_store()
    store.delete_by_doc_id(doc_id)
    store.save(str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(sa.text("SELECT file_path FROM user_docs WHERE doc_id=:d"), {"d": doc_id}).fetchone()
            if row:
                Path(row[0]).unlink(missing_ok=True)
            conn.execute(sa.text("DELETE FROM user_docs WHERE doc_id=:d"), {"d": doc_id})
        engine.dispose()
    except Exception:
        pass
    return {"ok": True}
