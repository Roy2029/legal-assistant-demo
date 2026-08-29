"""合同审查 API（D11）：上传→可配置脱敏→ReAct 审查→版本产物→下载/还原→删除。"""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .db import get_engine
from .llm import llm_client
from .session_utils import append_message, ensure_session
import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "contracts" / "raw"
MAPPING_ROOT = PROJECT_ROOT / "data" / "contracts" / "mappings"
REPORT_ROOT = PROJECT_ROOT / "data" / "contracts" / "reports"
RESTORED_ROOT = PROJECT_ROOT / "data" / "contracts" / "restored"
MASK_STATE_ROOT = PROJECT_ROOT / "data" / "contracts" / "mask_state"
WORKSPACE_ROOT = PROJECT_ROOT / "data" / "agent_workspace"
USER_RULES_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_rules"
USER_SKILLS_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_skills"
USER_RULE_FILES_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_rule_files"
BUILTIN_RULES = PROJECT_ROOT / "skills" / "contract_review" / "rules.jsonl"

SUPPORTED = {".docx", ".pdf", ".txt", ".md"}
router = APIRouter(prefix="/api/contracts")


def _cid_dir(cid: str) -> Path:
    return WORKSPACE_ROOT / f"contract-{cid}"


def _redacted_dir(cid: str) -> Path:
    return _cid_dir(cid) / "contracts"


def _raw_dir(cid: str) -> Path:
    return RAW_ROOT / cid


def _report_dir(cid: str) -> Path:
    return REPORT_ROOT / cid


def _restored_dir(cid: str) -> Path:
    return RESTORED_ROOT / cid


def _mapping_dir(cid: str) -> Path:
    return MAPPING_ROOT / cid


def _mask_state_path(cid: str) -> Path:
    return MASK_STATE_ROOT / f"{cid}.json"


def _insert_contract(contract_id: str, original_name: str, file_type: str, redacted_path: str, mapping_path: str, status: str = "uploaded"):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT OR REPLACE INTO contracts (contract_id, original_name, file_type, status, redacted_path, mapping_path) "
            "VALUES (:c, :o, :t, :s, :r, :m)"
        ), {"c": contract_id, "o": original_name, "t": file_type, "s": status, "r": redacted_path, "m": mapping_path})
    engine.dispose()


def _update_contract(contract_id: str, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE contracts SET {sets} WHERE contract_id=:cid"), {**fields, "cid": contract_id})
    engine.dispose()


def _get_contract(contract_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text(
            "SELECT contract_id, original_name, file_type, status, redacted_path, mapping_path, report_path, risk_count, created_at "
            "FROM contracts WHERE contract_id=:c"
        ), {"c": contract_id}).fetchone()
    engine.dispose()
    return dict(row._mapping) if row else None


def _list_redacted_files(cid: str) -> list[Path]:
    d = _redacted_dir(cid)
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.is_file()])


def _list_raw_files(cid: str) -> list[Path]:
    d = _raw_dir(cid)
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.is_file()])


def _list_dir_files(d: Path) -> list[Path]:
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.is_file()])


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx
        doc = docx.Document(str(path))
        return chr(10).join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == ".pdf":
        import fitz
        with fitz.open(str(path)) as pdf:
            return chr(10).join(page.get_text() for page in pdf)
    return path.read_text(encoding="utf-8", errors="ignore")


VERSION_LABELS = {
    "original": "原版",
    "redacted": "脱敏(初始)",
    "masked": "脱敏",
    "edited": "编辑版",
    "annotated": "批注",
    "report": "报告",
    "restored": "还原",
}


def _classify_versions(cid: str) -> list[dict]:
    """汇总合同全生命周期产物版本。"""
    versions = []
    for p in _list_raw_files(cid):
        versions.append({"kind": "original", "label": VERSION_LABELS["original"], "file": p.name, "path": str(p)})

    for p in _list_redacted_files(cid):
        name = p.name
        if "批注版" in name:
            kind = "annotated"
        elif "脱敏版" in name:
            kind = "masked"
        elif "编辑版" in name:
            kind = "edited"
        else:
            kind = "redacted"
        versions.append({"kind": kind, "label": VERSION_LABELS[kind], "file": name, "path": str(p)})

    for p in _list_dir_files(_report_dir(cid)):
        if "批注版" in p.name:
            kind = "annotated"
        elif p.name == "report.md" or "报告" in p.name:
            kind = "report"
        else:
            kind = "report"
        versions.append({"kind": kind, "label": VERSION_LABELS[kind], "file": p.name, "path": str(p)})

    for p in _list_dir_files(_restored_dir(cid)):
        versions.append({"kind": "restored", "label": VERSION_LABELS["restored"], "file": p.name, "path": str(p)})
    return versions


def _resolve_version(cid: str, kind: str, file: str) -> Path | None:
    if kind == "original":
        for p in _list_raw_files(cid):
            if p.name == file:
                return p
    elif kind in ("redacted", "masked", "edited"):
        for p in _list_redacted_files(cid):
            if p.name == file:
                return p
    elif kind == "annotated":
        # 批注版可能在工作区（Markdown）或报告目录（DOCX）
        for p in _list_redacted_files(cid):
            if p.name == file:
                return p
        for p in _list_dir_files(_report_dir(cid)):
            if p.name == file:
                return p
    elif kind == "report":
        for p in _list_dir_files(_report_dir(cid)):
            if p.name == file:
                return p
    elif kind == "restored":
        for p in _list_dir_files(_restored_dir(cid)):
            if p.name == file:
                return p
    return None


def _content_for(cid: str, kind: str, file: str) -> str | None:
    p = _resolve_version(cid, kind, file)
    if p is None:
        return None
    return _extract_text(p)


def _save_mask_mapping(cid: str, entries: list) -> Path:
    p = _mapping_dir(cid) / "mask_mapping.json"
    _write_json(p, {"entries": entries})
    return p


def _load_mask_mapping(cid: str) -> list:
    data = _read_json(_mapping_dir(cid) / "mask_mapping.json")
    return data.get("entries", []) if data else []


# ---------------------------------------------------------------------------
# 合同文档：上传 / 列表 / 重命名 / 删除 / 版本 / 内容
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_contracts(files: list[UploadFile] = File(...)):
    """上传一份或多份 docx/pdf/txt/md 合同：保存原件 → 初始自动脱敏 → 入工作区。"""
    from online_core import legal_mask

    results = []
    for file in files:
        filename = Path(file.filename or "untitled").name
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED:
            results.append({"filename": filename, "ok": False, "error": f"不支持 {ext}，仅支持 docx/pdf/txt/md"})
            continue

        contract_id = uuid.uuid4().hex
        raw_dir = _raw_dir(contract_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename
        content = await file.read()
        raw_path.write_bytes(content)

        redacted_dir = _redacted_dir(contract_id)
        mapping_dir = _mapping_dir(contract_id)
        mapping_dir.mkdir(parents=True, exist_ok=True)

        result = legal_mask.process_document(str(raw_path), str(redacted_dir))
        if not result.success:
            shutil.rmtree(raw_dir, ignore_errors=True)
            results.append({"filename": filename, "ok": False, "error": result.error or "脱敏失败"})
            continue

        mapping_src = Path(result.mapping_file) if result.mapping_file else None
        mapping_dst = None
        if mapping_src and mapping_src.exists():
            mapping_dst = mapping_dir / mapping_src.name
            mapping_src.rename(mapping_dst)

        redacted_path = result.output_file or ""
        _insert_contract(contract_id, filename, ext, redacted_path, str(mapping_dst or ""), status="uploaded")
        _write_json(_mask_state_path(contract_id), {"pending_manual": [], "confirmed": False})
        results.append({"filename": filename, "ok": True, "contract_id": contract_id, "redacted": Path(redacted_path).name if redacted_path else ""})

    return {"ok": True, "data": results}


@router.get("")
def list_contracts():
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT contract_id, original_name, file_type, status, redacted_path, mapping_path, report_path, risk_count, created_at "
            "FROM contracts ORDER BY created_at DESC"
        )).fetchall()
    engine.dispose()
    return {"ok": True, "data": [dict(r._mapping) for r in rows]}


@router.put("/{contract_id}")
def rename_contract(contract_id: str, payload: dict):
    name = (payload.get("original_name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": {"code": "empty_name", "message": "名称不能为空"}}, status_code=400)
    row = _get_contract(contract_id)
    if not row:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    _update_contract(contract_id, original_name=name)
    return {"ok": True, "data": {"contract_id": contract_id, "original_name": name}}


@router.delete("/{contract_id}")
def delete_contract(contract_id: str):
    row = _get_contract(contract_id)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM contracts WHERE contract_id=:c"), {"c": contract_id})
    engine.dispose()
    if row:
        for p in (row.get("redacted_path"), row.get("mapping_path"), row.get("report_path")):
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)
    for d in (_raw_dir(contract_id), _mapping_dir(contract_id), _report_dir(contract_id),
              _restored_dir(contract_id), _cid_dir(contract_id)):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    _mask_state_path(contract_id).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 规则库 / skill 文件
# ---------------------------------------------------------------------------

@router.get("/skills")
def list_user_skills():
    USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    USER_RULE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    rules = [p.name for p in sorted(USER_RULES_DIR.glob("*.jsonl"))]
    skills = [p.name for p in sorted(USER_SKILLS_DIR.glob("*.md"))]
    txt_files = [p.name for p in sorted(USER_RULE_FILES_DIR.glob("*.txt"))]
    library = sorted(set(rules + skills + txt_files))
    return {"ok": True, "data": {"rules": rules, "skills": skills, "library": library}}


def _save_skill_file(filename: str, content: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext == ".jsonl":
        USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_RULES_DIR / filename
        try:
            for line in content.decode("utf-8").splitlines():
                if line.strip():
                    json.loads(line)
        except Exception as e:
            raise ValueError(f"JSONL 解析失败: {e}")
        dst.write_bytes(content)
        return {"filename": filename, "type": "rules"}
    if ext == ".md":
        USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_SKILLS_DIR / filename
        dst.write_bytes(content)
        return {"filename": filename, "type": "skill"}
    if ext == ".txt":
        USER_RULE_FILES_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_RULE_FILES_DIR / filename
        dst.write_bytes(content)
        return {"filename": filename, "type": "rule_file"}
    raise ValueError("仅支持 .jsonl / .md / .txt 规则库文件")


@router.post("/skills")
async def upload_skill(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
):
    """上传规则库文件：.jsonl / .md / .txt，支持多选。"""
    items = []
    if files:
        items.extend(files)
    if file:
        items.append(file)
    if not items:
        return JSONResponse({"ok": False, "error": {"code": "empty", "message": "未选择文件"}}, status_code=400)
    results = []
    errors = []
    for f in items:
        filename = Path(f.filename or "untitled").name
        content = await f.read()
        try:
            item = _save_skill_file(filename, content)
            results.append(item)
        except Exception as e:
            errors.append({"filename": filename, "error": str(e)})
    if len(items) == 1 and errors:
        return JSONResponse({"ok": False, "error": {"code": "bad_format", "message": errors[0]["error"]}}, status_code=400)
    return {"ok": True, "data": results if len(items) > 1 else results[0] if results else None, "errors": errors}


@router.get("/{contract_id}/files")
def list_contract_files(contract_id: str):
    files = [p.name for p in _list_redacted_files(contract_id)]
    return {"ok": True, "data": files}


@router.get("/{contract_id}/versions")
def list_versions(contract_id: str):
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    return {"ok": True, "data": _classify_versions(contract_id)}


@router.get("/{contract_id}/content")
def get_contract_content(contract_id: str, file: str, kind: str = "redacted"):
    """读取版本内容。kind: original/redacted/masked/edited/annotated/report/restored。"""
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    content = _content_for(contract_id, kind, file)
    if content is None:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "文件不存在"}}, status_code=404)
    return {"ok": True, "data": {"file": file, "kind": kind, "content": content}}


@router.put("/{contract_id}/content")
async def update_contract_content(contract_id: str, payload: dict):
    """把编辑后的文本保存为工作区内的 Markdown 文件。"""
    file = (payload.get("file") or "").strip()
    content = payload.get("content") or ""
    if not file:
        return JSONResponse({"ok": False, "error": {"code": "empty_file", "message": "file 不能为空"}}, status_code=400)
    files = _list_redacted_files(contract_id)
    if not any(p.name == file for p in files):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "文件不存在"}}, status_code=404)
    stem = Path(file).stem
    out = _redacted_dir(contract_id) / f"{stem}_编辑版.md"
    out.write_text(content, encoding="utf-8")
    return {"ok": True, "data": {"file": out.name}}


# ---------------------------------------------------------------------------
# 可配置脱敏：扫描 / 确认脱敏 / 映射配置 / 手动片段 / 按配置还原
# ---------------------------------------------------------------------------

@router.get("/{contract_id}/scan")
def scan_contract_pii(contract_id: str, file: str | None = None, categories: str | None = None):
    """扫描原件中的敏感信息，返回可勾选清单。"""
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    raw_files = _list_raw_files(contract_id)
    if not raw_files:
        return JSONResponse({"ok": False, "error": {"code": "no_raw", "message": "原件不存在"}}, status_code=404)
    target = raw_files[0]
    if file:
        for p in raw_files:
            if p.name == file:
                target = p
                break
    text = _extract_text(target)
    from online_core import legal_mask_config
    cat_list = [c.strip() for c in (categories or "").split(",") if c.strip()] or None
    items = legal_mask_config.scan_pii(text, cat_list)
    return {"ok": True, "data": {"file": target.name, "items": items, "total": len(items)}}


@router.post("/{contract_id}/mask")
def confirm_mask(contract_id: str, payload: dict):
    """按用户配置执行脱敏，生成脱敏版并保存脱密映射配置。"""
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    raw_files = _list_raw_files(contract_id)
    if not raw_files:
        return JSONResponse({"ok": False, "error": {"code": "no_raw", "message": "原件不存在"}}, status_code=404)

    file = payload.get("file") or raw_files[0].name
    target = raw_files[0]
    for p in raw_files:
        if p.name == file:
            target = p
            break
    text = _extract_text(target)

    categories = payload.get("categories") or [c["key"] for c in __import__("online_core.legal_mask_config", fromlist=["MASK_CATEGORIES"]).MASK_CATEGORIES]
    method = payload.get("method") or "placeholder"
    items = payload.get("items") or []
    manual_items = payload.get("manual_items") or []

    # 未勾选任何项目时，按所选 categories 全量扫描后脱敏
    if not items and not manual_items:
        from online_core import legal_mask_config
        items = legal_mask_config.scan_pii(text, categories)

    from online_core import legal_mask_config
    # 手动片段已由前端放进 items 时去重（按 id）
    combined = list(items)
    seen_ids = {x.get("id") for x in combined if x.get("id")}
    for m in manual_items:
        if m.get("id") not in seen_ids:
            combined.append(m)
            seen_ids.add(m.get("id"))
    masked_text, entries = legal_mask_config.mask_text(text, combined, method)
    stem = Path(target.name).stem
    out = _redacted_dir(contract_id) / f"{stem}_脱敏版.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(masked_text, encoding="utf-8")
    mapping_path = _save_mask_mapping(contract_id, entries)
    _update_contract(contract_id, status="masked", redacted_path=str(out))
    return {"ok": True, "data": {"file": out.name, "content": masked_text, "mapping": entries, "mapping_path": str(mapping_path), "masked_count": len(entries)}}


@router.get("/{contract_id}/mask/mapping")
def get_mask_mapping(contract_id: str):
    entries = _load_mask_mapping(contract_id)
    return {"ok": True, "data": {"entries": entries}}


@router.post("/{contract_id}/mask/manual")
def add_manual_mask_item(contract_id: str, payload: dict):
    """把用户在预览中拖选的原文片段加入脱敏清单。"""
    text = (payload.get("text") or "").strip()
    category = payload.get("category") or "manual"
    if not text:
        return JSONResponse({"ok": False, "error": {"code": "empty_text", "message": "片段不能为空"}}, status_code=400)
    state = _read_json(_mask_state_path(contract_id))
    pending = state.get("pending_manual", [])
    item_id = f"manual_{len(pending) + 1}"
    item = {"id": item_id, "category": category, "value": text, "start": -1, "end": -1}
    pending.append(item)
    state["pending_manual"] = pending
    _write_json(_mask_state_path(contract_id), state)
    return {"ok": True, "data": item}


@router.get("/{contract_id}/mask/manual")
def list_manual_mask_items(contract_id: str):
    state = _read_json(_mask_state_path(contract_id))
    return {"ok": True, "data": state.get("pending_manual", [])}


@router.delete("/{contract_id}/mask/manual/{item_id}")
def delete_manual_mask_item(contract_id: str, item_id: str):
    state = _read_json(_mask_state_path(contract_id))
    pending = state.get("pending_manual", [])
    state["pending_manual"] = [x for x in pending if x.get("id") != item_id]
    _write_json(_mask_state_path(contract_id), state)
    return {"ok": True}


@router.post("/{contract_id}/mask/restore")
def restore_selected_mapping(contract_id: str, payload: dict):
    """按选中的脱密映射配置进行还原，还原结果保存在 agent 工作区之外。"""
    file = (payload.get("file") or "").strip()
    entries = payload.get("entries") or []
    if not file:
        return JSONResponse({"ok": False, "error": {"code": "empty_file", "message": "file 不能为空"}}, status_code=400)
    masked = _content_for(contract_id, "masked", file)
    if masked is None:
        masked = _content_for(contract_id, "redacted", file)
    if masked is None:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "脱敏文件不存在"}}, status_code=404)
    from online_core import legal_mask_config
    restored, warnings = legal_mask_config.restore_masked(masked, entries)
    stem = Path(file).stem
    out = _restored_dir(contract_id) / f"{stem}_还原版.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(restored, encoding="utf-8")
    return {"ok": True, "data": {"file": out.name, "content": restored, "warnings": warnings}}


# ---------------------------------------------------------------------------
# 审查：规则引擎（M1）与报告 / 下载
# ---------------------------------------------------------------------------

def _run_rule_review(cid: str) -> dict:
    from online_core import contract_rules
    files = _list_redacted_files(cid)
    if not files:
        return {"ok": False, "error": "没有可审查的脱敏文件"}
    risks = []
    for path in files:
        text = _extract_text(path)
        risks.extend(contract_rules.scan_text(text, file_name=path.name))
    report = contract_rules.render_report([p.name for p in files], risks)

    report_dir = _report_dir(cid)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    high = sum(1 for r in risks if r["risk_level"] == "high")
    medium = sum(1 for r in risks if r["risk_level"] == "medium")
    low = sum(1 for r in risks if r["risk_level"] == "low")
    _update_contract(cid, status="reviewed", report_path=str(report_path), risk_count=len(risks))
    return {"ok": True, "risk_count": len(risks), "high": high, "medium": medium, "low": low, "report": report, "risks": risks}


@router.post("/{contract_id}/review")
def review_contract(contract_id: str):
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    result = _run_rule_review(contract_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.get("/{contract_id}/report")
def get_report(contract_id: str):
    row = _get_contract(contract_id)
    if not row or not row.get("report_path") or not Path(row["report_path"]).exists():
        return JSONResponse({"ok": False, "error": {"code": "no_report", "message": "报告不存在，请先审查"}}, status_code=404)
    return FileResponse(row["report_path"], filename=f"{Path(row['original_name']).stem}_审查报告.md", media_type="text/markdown")


@router.get("/{contract_id}/download")
def download_contract(contract_id: str, kind: str = "redacted", file: str | None = None):
    row = _get_contract(contract_id)
    if not row:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)

    if kind == "original":
        raw_files = _list_raw_files(contract_id)
        if file:
            raw_files = [p for p in raw_files if p.name == file]
        if not raw_files:
            return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "原件不存在"}}, status_code=404)
        return FileResponse(raw_files[0], filename=raw_files[0].name)

    if kind == "annotated":
        p = None
        for v in _classify_versions(contract_id):
            if v["kind"] == "annotated":
                p = Path(v["path"])
                if file and v["file"] != file:
                    continue
                break
        if p is None:
            # 退回规则引擎批注 DOCX（旧流程）
            files = _list_redacted_files(contract_id)
            if file:
                files = [x for x in files if x.name == file]
            docx_files = [x for x in files if x.suffix.lower() == ".docx"]
            if not docx_files:
                return JSONResponse({"ok": False, "error": {"code": "no_docx", "message": "批注版仅支持 DOCX 合同"}}, status_code=404)
            from online_core import contract_rules
            out_dir = _report_dir(contract_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{docx_files[0].stem}_批注版.docx"
            n = contract_rules.annotate_docx(docx_files[0], out_path)
            if n == 0:
                return JSONResponse({"ok": False, "error": {"code": "no_risk", "message": "未命中风险，无需批注"}}, status_code=404)
            return FileResponse(out_path, filename=out_path.name)
        return FileResponse(p, filename=p.name)

    if kind == "restored":
        files = _list_dir_files(_restored_dir(contract_id))
        if file:
            files = [p for p in files if p.name == file]
        if files:
            return FileResponse(files[0], filename=files[0].name)
        # 旧流程：用 legacy mapping 还原脱敏文本
        files = _list_redacted_files(contract_id)
        if file:
            files = [p for p in files if p.name == file]
        if not files:
            return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "脱敏文件不存在"}}, status_code=404)
        mapping = {}
        if row.get("mapping_path") and Path(row["mapping_path"]).exists():
            try:
                mapping = json.loads(Path(row["mapping_path"]).read_text(encoding="utf-8"))
            except Exception:
                mapping = {}
        from online_core.legal_mask import restore_text
        restored_parts = []
        for p in files:
            text = _extract_text(p)
            restored = restore_text(text, mapping)
            restored_parts.append(f"## {p.name}\n\n{restored}")
        out = _report_dir(contract_id) / "restored.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(chr(10).join(restored_parts), encoding="utf-8")
        return FileResponse(out, filename=f"{Path(row['original_name']).stem}_还原版.txt", media_type="text/plain")

    if kind == "masked":
        for v in _classify_versions(contract_id):
            if v["kind"] == "masked":
                if file and v["file"] != file:
                    continue
                return FileResponse(Path(v["path"]), filename=v["file"])
        return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "脱敏文件不存在"}}, status_code=404)

    files = _list_redacted_files(contract_id)
    if file:
        files = [p for p in files if p.name == file]
    if not files:
        return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "脱敏文件不存在"}}, status_code=404)
    return FileResponse(files[0], filename=files[0].name)


# ---------------------------------------------------------------------------
# ReAct 合同审查 agent：非流式（兼容旧接口） + 流式聊天
# ---------------------------------------------------------------------------

@router.post("/{contract_id}/agent-review")
async def agent_review_contract(contract_id: str):
    """LLM 驱动的合同审查 agent（ReAct）。未配置 LLM 时回退规则引擎。"""
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    if not llm_client.configured:
        result = _run_rule_review(contract_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        result["agent"] = "rule_engine_fallback"
        return result

    from online_core.agents.contract_agent import ContractAgent
    agent = ContractAgent(contract_id=contract_id)
    result = await agent.run("请审查工作区内的合同并提交报告。")
    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": {"code": "agent_failed", "message": result.get("report", "审查失败")}}, status_code=400)

    report_dir = _report_dir(contract_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"
    report_path.write_text(result.get("report", ""), encoding="utf-8")
    risks = result.get("risks") or []
    high = sum(1 for r in risks if r.get("risk_level") == "high")
    medium = sum(1 for r in risks if r.get("risk_level") == "medium")
    low = sum(1 for r in risks if r.get("risk_level") == "low")
    _update_contract(contract_id, status="reviewed", report_path=str(report_path), risk_count=len(risks))
    return {"ok": True, "agent": "contract_review", "risk_count": len(risks), "high": high, "medium": medium, "low": low, "report": result.get("report", ""), "answer": result.get("answer", ""), "needs_human": result.get("needs_human", False), "risks": risks}


@router.post("/{contract_id}/agent-chat")
async def contract_agent_chat(contract_id: str, payload: dict):
    """审查 tab 的 ReAct agent chat：流式 SSE，自动附带当前脱敏文件与规则库文件引用。"""
    query = (payload.get("query") or "").strip()
    if not query:
        return JSONResponse({"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}, status_code=400)
    if not _get_contract(contract_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)

    current_file = payload.get("file") or ""
    rule_files = payload.get("rule_files") or []
    session_id = ensure_session(payload.get("session_id"), mode="contract_review", action=contract_id, title=query[:20] or "合同审查会话")
    refs = []
    if current_file:
        refs.append(f"脱敏文件：{current_file}")
    if rule_files:
        refs.append(f"规则库：{', '.join(rule_files)}")
    user_text = query if not refs else query + "（" + "；".join(refs) + "）"
    append_message(session_id, "user", user_text, msg_kind="user")

    async def event_gen():
        yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id, 'contract_id': contract_id, 'file': current_file, 'rule_files': rule_files}, ensure_ascii=False)}\n\n"

        if not llm_client.configured:
            msg = "LLM 未配置，请在设置页填写 Base URL / API Key / Model"
            append_message(session_id, "assistant", msg, msg_kind="final")
            yield f"data: {json.dumps({'type': 'error', 'code': 'llm_not_configured', 'message': msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        from online_core.agents.contract_agent import ContractAgent
        agent = ContractAgent(contract_id=contract_id, current_file=current_file, rule_files=rule_files)
        q = asyncio.Queue()

        async def cb(evt):
            await q.put(evt)

        task = asyncio.create_task(agent.run(query, event_cb=cb))
        while not task.done() or not q.empty():
            try:
                evt = await asyncio.wait_for(q.get(), timeout=0.2)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                continue
        try:
            result = task.result()
        except Exception as e:
            append_message(session_id, "assistant", f"审查失败：{e}", msg_kind="final")
            yield f"data: {json.dumps({'type': 'error', 'code': 'agent_failed', 'message': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        report = result.get("report", "") if result.get("ok") else result.get("report", "")
        answer = result.get("answer", "")
        risks = result.get("risks") or []

        if result.get("ok"):
            report_dir = _report_dir(contract_id)
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / "report.md"
            report_path.write_text(report or answer, encoding="utf-8")
            _update_contract(contract_id, status="reviewed", report_path=str(report_path), risk_count=len(risks))
            # 基于脱敏文本 + 最终风险清单，统一生成批注版 Markdown（工作区）和 DOCX（报告目录）
            if current_file:
                masked_text = _content_for(contract_id, "masked", current_file)
                if masked_text is None:
                    masked_text = _content_for(contract_id, "redacted", current_file)
                if masked_text:
                    from online_core import contract_rules
                    if not risks:
                        risks = contract_rules.scan_text(masked_text, file_name=current_file)
                    try:
                        annotated_md = contract_rules.annotate_text_markdown(masked_text, risks)
                        md_path = _redacted_dir(contract_id) / f"{Path(current_file).stem}_批注版.md"
                        md_path.parent.mkdir(parents=True, exist_ok=True)
                        md_path.write_text(annotated_md, encoding="utf-8")
                    except Exception:
                        pass
                    out_path = report_dir / f"{Path(current_file).stem}_批注版.docx"
                    try:
                        contract_rules.annotate_text_docx(masked_text, risks, out_path)
                    except Exception:
                        pass

        final_text = answer or report or "审查完成"
        append_message(session_id, "assistant", final_text[:20000], msg_kind="final")
        yield f"data: {json.dumps({'type': 'agent_report', 'agent': 'contract_review', 'answer': answer, 'report': report, 'risks': risks, 'needs_human': result.get('needs_human', False)}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'final', 'answer': final_text, 'report': report, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
