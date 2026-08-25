"""用户词典 API（D02 §4.8）。"""
from fastapi import APIRouter
from online_core.lexicon_service import add_term, remove_term, set_enabled, list_terms

router = APIRouter(prefix="/api/lexicon")


@router.get("")
def get_lexicon():
    return {"ok": True, "data": list_terms()}


@router.post("")
def post_term(payload: dict):
    term = (payload.get("term") or "").strip()
    if not term:
        return {"ok": False, "error": {"code": "empty_term", "message": "term 不能为空"}}
    add_term(term)
    return {"ok": True, "data": list_terms()}


@router.delete("/{term}")
def delete_term(term: str):
    remove_term(term)
    return {"ok": True, "data": list_terms()}


@router.put("/{term}")
def put_term(term: str, payload: dict):
    set_enabled(term, bool(payload.get("enabled", True)))
    return {"ok": True, "data": list_terms()}
