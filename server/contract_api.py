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
USER_SKILLS_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_skills"
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
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "data": {
        "rules": [p.name for p in sorted(USER_RULES_DIR.glob("*.jsonl"))],
        "skills": [p.name for p in sorted(USER_SKILLS_DIR.glob("*.md"))],
    }}


@router.post("/skills")
async def upload_skill(file: UploadFile = File(...)):
    """上传用户自定义合同审查规则（.jsonl）或领域/流程 skill（.md）。"""
    filename = Path(file.filename or "untitled").name
    ext = Path(filename).suffix.lower()
    if ext == ".jsonl":
        USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_RULES_DIR / filename
        content = await file.read()
        try:
            for line in content.decode("utf-8").splitlines():
                if line.strip():
                    json.loads(line)
        except Exception as e:
            return JSONResponse({"ok": False, "error": {"code": "bad_jsonl", "message": f"JSONL 解析失败: {e}"}}, status_code=400)
        dst.write_bytes(content)
        return {"ok": True, "data": {"filename": filename, "type": "rules"}}
    if ext == ".md":
        USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_SKILLS_DIR / filename
        content = await file.read()
        dst.write_bytes(content)
        return {"ok": True, "data": {"filename": filename, "type": "skill"}}
    return JSONResponse({"ok": False, "error": {"code": "bad_format", "message": "仅支持 .jsonl 规则文件或 .md skill 文件"}}, status_code=400)


def _run_rule_review(cid: str) -> dict:
    from online_core import contract_rules
    files = _list_redacted_files(cid)
    if not files:
        return {"ok": False, "error": "没有可审查的脱敏文件"}
    risks = []
    for path in files:
        text = _extract_text(path)
        risks.extend(contract_rules.scan_text(text, file_name=path.name))
    risks = []
    for path in files:
        text = _extract_text(path)
        risks.extend(contract_rules.scan_text(text, file_name=path.name))
    report = contract_rules.render_report([p.name for p in files], risks)

    report_dir = REPORT_ROOT / cid
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


@router.post("/{contract_id}/agent-review")
async def agent_review_contract(contract_id: str):
    """LLM 驱动的合同审查 agent（ReAct）。未配置 LLM 时回退规则引擎。"""
    from server.llm import llm_client
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

    report_dir = REPORT_ROOT / contract_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"
    report_path.write_text(result.get("report", ""), encoding="utf-8")
    risks = result.get("risks") or []
    high = sum(1 for r in risks if r.get("risk_level") == "high")
    medium = sum(1 for r in risks if r.get("risk_level") == "medium")
    low = sum(1 for r in risks if r.get("risk_level") == "low")
    _update_contract(contract_id, status="reviewed", report_path=str(report_path), risk_count=len(risks))
    return {"ok": True, "agent": "contract_review", "risk_count": len(risks), "high": high, "medium": medium, "low": low, "report": result.get("report", ""), "answer": result.get("answer", ""), "needs_human": result.get("needs_human", False), "risks": risks}


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
