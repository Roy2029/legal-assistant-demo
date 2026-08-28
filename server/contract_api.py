"""合同审查 agent API：上传→脱敏→审查→报告→下载/还原→删除。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .db import get_engine
import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "contracts" / "raw"
MAPPING_ROOT = PROJECT_ROOT / "data" / "contracts" / "mappings"
REPORT_ROOT = PROJECT_ROOT / "data" / "contracts" / "reports"
WORKSPACE_ROOT = PROJECT_ROOT / "data" / "agent_workspace"
USER_RULES_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_rules"
BUILTIN_RULES = PROJECT_ROOT / "skills" / "contract_review" / "rules.jsonl"

SUPPORTED = {".docx", ".pdf"}

router = APIRouter(prefix="/api/contracts")


def _cid_dir(cid: str) -> Path:
    return WORKSPACE_ROOT / f"contract-{cid}"


def _redacted_dir(cid: str) -> Path:
    return _cid_dir(cid) / "contracts"


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


def _list_redacted_files(cid: str) -> list[Path]:
    d = _redacted_dir(cid)
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.is_file()])


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == ".pdf":
        import fitz
        with fitz.open(str(path)) as pdf:
            return "\n".join(page.get_text() for page in pdf)
    return path.read_text(encoding="utf-8", errors="ignore")


@router.post("/upload")
async def upload_contracts(files: list[UploadFile] = File(...)):
    """上传一份或多份 docx/pdf 合同：保存原件 → LegalMask 脱敏 → 入工作区。"""
    from online_core import legal_mask

    results = []
    for file in files:
        filename = Path(file.filename or "untitled").name
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED:
            results.append({"filename": filename, "ok": False, "error": f"不支持 {ext}，仅支持 docx/pdf"})
            continue

        contract_id = uuid.uuid4().hex
        raw_dir = RAW_ROOT / contract_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename
        content = await file.read()
        raw_path.write_bytes(content)

        redacted_dir = _redacted_dir(contract_id)
        mapping_dir = MAPPING_ROOT / contract_id
        mapping_dir.mkdir(parents=True, exist_ok=True)

        result = legal_mask.process_document(str(raw_path), str(redacted_dir))
        if not result.success:
            # 清理
            for p in raw_dir.iterdir():
                p.unlink(missing_ok=True)
            raw_dir.rmdir(ignore_errors=True)
            results.append({"filename": filename, "ok": False, "error": result.error or "脱敏失败"})
            continue

        # 把 mapping 移到 agent 不可访问目录
        mapping_src = Path(result.mapping_file) if result.mapping_file else None
        mapping_dst = None
        if mapping_src and mapping_src.exists():
            mapping_dst = mapping_dir / mapping_src.name
            mapping_src.rename(mapping_dst)

        redacted_path = result.output_file or ""
        _insert_contract(contract_id, filename, ext, redacted_path, str(mapping_dst or ""), status="uploaded")
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


@router.get("/skills")
def list_user_skills():
    USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "data": [p.name for p in sorted(USER_RULES_DIR.glob("*.jsonl"))]}


@router.post("/skills")
async def upload_skill(file: UploadFile = File(...)):
    """上传用户自定义合同审查规则（JSONL，字段与 rules.jsonl 一致）。"""
    filename = Path(file.filename or "untitled").name
    if Path(filename).suffix.lower() != ".jsonl":
        return JSONResponse({"ok": False, "error": {"code": "bad_format", "message": "仅支持 .jsonl 规则文件"}}, status_code=400)
    USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
    dst = USER_RULES_DIR / filename
    content = await file.read()
    # 校验 JSONL
    try:
        for line in content.decode("utf-8").splitlines():
            if line.strip():
                json.loads(line)
    except Exception as e:
        return JSONResponse({"ok": False, "error": {"code": "bad_jsonl", "message": f"JSONL 解析失败: {e}"}}, status_code=400)
    dst.write_bytes(content)
    return {"ok": True, "data": {"filename": filename}}


def _load_rules() -> list[dict]:
    rules = []
    for path in [BUILTIN_RULES, *sorted(USER_RULES_DIR.glob("*.jsonl"))]:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rule = json.loads(line)
                if rule.get("status", "enabled") != "disabled":
                    rules.append(rule)
        except Exception:
            continue
    return rules


def _run_rule_review(cid: str) -> dict:
    files = _list_redacted_files(cid)
    if not files:
        return {"ok": False, "error": "没有可审查的脱敏文件"}

    rules = _load_rules()
    risks = []
    for path in files:
        text = _extract_text(path)
        for rule in rules:
            patterns = (rule.get("trigger") or {}).get("patterns") or []
            for pat in patterns:
                if not pat:
                    continue
                idx = text.find(pat)
                if idx >= 0:
                    snippet = text[max(0, idx - 40):idx + 120].replace("\n", " ")
                    risks.append({
                        "rule_id": rule.get("rule_id"),
                        "dimension": rule.get("dimension"),
                        "risk_level": rule.get("risk_level"),
                        "risk_desc": rule.get("risk_desc"),
                        "suggestion": rule.get("suggestion_template"),
                        "basis": rule.get("basis") or [],
                        "source": rule.get("source", ""),
                        "file": path.name,
                        "snippet": snippet,
                    })
                    break  # 同一规则同一文件只记一次

    # 去重：同一 rule_id + file 只保留一条
    seen = set()
    deduped = []
    for r in risks:
        key = (r["rule_id"], r["file"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    risks = deduped

    high = [r for r in risks if r["risk_level"] == "high"]
    medium = [r for r in risks if r["risk_level"] == "medium"]
    low = [r for r in risks if r["risk_level"] == "low"]

    report = f"# 合同审查报告\n\n"
    report += f"- 合同：{', '.join(p.name for p in files)}\n"
    report += f"- 风险总数：{len(risks)}（高 {len(high)} / 中 {len(medium)} / 低 {len(low)}）\n\n"
    report += "## 风险清单\n\n"
    if not risks:
        report += "未命中内置规则。\n"
    for r in risks:
        report += f"### [{r['risk_level']}] {r['dimension']} — {r['rule_id']}\n"
        report += f"- 说明：{r['risk_desc']}\n"
        report += f"- 建议：{r['suggestion']}\n"
        report += f"- 原文片段：{r['snippet']}\n"
        if r["basis"]:
            report += f"- 依据：{r['basis']}\n"
        report += "\n"
    report += "---\n本报告由规则引擎生成，仅供参考，使用前须经执业律师核阅。\n"

    report_dir = REPORT_ROOT / cid
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    _update_contract(cid, status="reviewed", report_path=str(report_path), risk_count=len(risks))
    return {"ok": True, "risk_count": len(risks), "high": len(high), "medium": len(medium), "low": len(low), "report": report, "risks": risks}


@router.post("/{contract_id}/review")
def review_contract(contract_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT 1 FROM contracts WHERE contract_id=:c"), {"c": contract_id}).fetchone()
    engine.dispose()
    if not row:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)
    result = _run_rule_review(contract_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.get("/{contract_id}/report")
def get_report(contract_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT report_path, original_name FROM contracts WHERE contract_id=:c"), {"c": contract_id}).fetchone()
    engine.dispose()
    if not row or not row[0] or not Path(row[0]).exists():
        return JSONResponse({"ok": False, "error": {"code": "no_report", "message": "报告不存在，请先审查"}}, status_code=404)
    return FileResponse(row[0], filename=f"{Path(row[1]).stem}_审查报告.md", media_type="text/markdown")


@router.get("/{contract_id}/download")
def download_contract(contract_id: str, kind: str = "redacted", file: str | None = None):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT original_name, redacted_path, mapping_path FROM contracts WHERE contract_id=:c"), {"c": contract_id}).fetchone()
    engine.dispose()
    if not row:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "合同不存在"}}, status_code=404)

    if kind == "restored":
        # 读取脱敏文本，用 mapping 还原，输出 txt
        files = _list_redacted_files(contract_id)
        if file:
            files = [p for p in files if p.name == file]
        if not files:
            return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "脱敏文件不存在"}}, status_code=404)
        mapping = {}
        if row[2] and Path(row[2]).exists():
            try:
                mapping = json.loads(Path(row[2]).read_text(encoding="utf-8"))
            except Exception:
                mapping = {}
        from online_core.legal_mask import restore_text
        restored_parts = []
        for p in files:
            text = _extract_text(p)
            restored = restore_text(text, mapping)
            restored_parts.append(f"## {p.name}\n\n{restored}")
        out = REPORT_ROOT / contract_id / "restored.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n\n".join(restored_parts), encoding="utf-8")
        return FileResponse(out, filename=f"{Path(row[0]).stem}_还原版.txt", media_type="text/plain")

    files = _list_redacted_files(contract_id)
    if file:
        files = [p for p in files if p.name == file]
    if not files:
        return JSONResponse({"ok": False, "error": {"code": "no_file", "message": "脱敏文件不存在"}}, status_code=404)
    return FileResponse(files[0], filename=files[0].name)


@router.delete("/{contract_id}")
def delete_contract(contract_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT redacted_path, mapping_path, report_path FROM contracts WHERE contract_id=:c"), {"c": contract_id}).fetchone()
        conn.execute(sa.text("DELETE FROM contracts WHERE contract_id=:c"), {"c": contract_id})
    engine.dispose()
    if row:
        for p in (row[0], row[1], row[2]):
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)
    import shutil
    for d in (RAW_ROOT / contract_id, MAPPING_ROOT / contract_id, REPORT_ROOT / contract_id, _cid_dir(contract_id)):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}
