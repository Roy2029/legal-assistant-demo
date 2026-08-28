"""实务助手 API（W5 M0 桩）：skill 注册表 + 按 steps 调度工具。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .llm import llm_client

router = APIRouter(prefix="/api")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _parse_frontmatter(text: str) -> dict:
    parts = text.split("---")
    if len(parts) >= 3:
        return yaml.safe_load(parts[1]) or {}
    return {}


def list_skills() -> list[dict]:
    skills = []
    for md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
        skills.append({
            "skill_id": fm.get("skill_id"),
            "name": fm.get("name"),
            "description": fm.get("description"),
            "status": fm.get("status", "stub"),
            "steps": fm.get("steps", []),
        })
    return skills


def get_skill(skill_id: str) -> dict | None:
    for s in list_skills():
        if s["skill_id"] == skill_id:
            return s
    return None


def tool_kb_retrieval(query: str) -> dict:
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    out = svc.search(query)
    chunks = []
    for r in out.results[:5]:
        m = r.chunk.metadata or {}
        chunks.append({
            "law_name": m.get("law_name", ""),
            "article_no": m.get("article_no", ""),
            "text": r.chunk.text[:120],
        })
    return {"total": len(out.results), "chunks": chunks}


async def tool_search_law(query: str, session_id: str = "assistant") -> dict:
    from online_core.agents.rag_agent import RAGAgent
    agent = RAGAgent(session_id=session_id)
    result = await agent.run(query)
    return {
        "total": len(result.get("citations", [])),
        "answer": result.get("answer", ""),
        "report": result.get("report", ""),
        "citations": result.get("citations", []),
        "needs_human": bool(result.get("needs_human", False)),
        "trace": result.get("trace", {}),
    }


def tool_case_retrieval(query: str) -> dict:
    # M0：离线案例库未就绪，返回空态提示（不编造）
    return {"total": 0, "cases": [], "note": "案例库数据准备中，暂未接入离线案例"}


def analyze_step(query: str, tool_results: dict) -> str:
    if not llm_client.configured:
        return f"[M0 桩] 已检索到 {sum(len(v.get('chunks', [])) for v in tool_results.values())} 条法规/案例线索。深度分析在 M1 建设。"
    return f"[M0 桩] 已基于检索资料生成初步分析框架（工具结果 {json.dumps(tool_results, ensure_ascii=False)[:300]}）。"


async def assistant_event_gen(action: str, query: str):
    skill = get_skill(action)
    trace_id = uuid.uuid4().hex
    if skill is None:
        yield f"data: {json.dumps({'type': 'error', 'code': 'unknown_action', 'message': f'未找到业务动作 {action}'}, ensure_ascii=False)}\n\n"
        return
    yield f"data: {json.dumps({'type': 'session_start', 'action': action, 'skill_id': skill['skill_id'], 'trace_id': trace_id}, ensure_ascii=False)}\n\n"
    tool_results = {}
    for step in skill.get("steps", []):
        yield f"data: {json.dumps({'type': 'step_start', 'step': step.get('id')}, ensure_ascii=False)}\n\n"
        tool = step.get("tool")
        if tool == "kb_retrieval":
            yield f"data: {json.dumps({'type': 'tool_call', 'tool': tool, 'params': {'query': query}}, ensure_ascii=False)}\n\n"
            result = tool_kb_retrieval(query)
            tool_results["kb"] = result
            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool, 'summary': '命中 ' + str(result['total']) + ' 条'}, ensure_ascii=False)}\n\n"

        elif tool == "search_law":
            yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'knowledge', 'task': query}, ensure_ascii=False)}\n\n"
            result = await tool_search_law(query, session_id=trace_id)
            tool_results["search_law"] = result
            yield f"data: {json.dumps({'type': 'agent_trace', 'agent': 'knowledge', 'trace': result.get('trace', {})}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_report', 'agent': 'knowledge', 'answer': result.get('answer', ''), 'report': result.get('report', ''), 'citations': result.get('citations', []), 'needs_human': result.get('needs_human', False)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool, 'summary': ('检索报告已生成' if result.get('answer') else '检索未完成') + ('（需人工介入）' if result.get('needs_human') else '')}, ensure_ascii=False)}\n\n"

        elif tool == "case_retrieval":
            yield f"data: {json.dumps({'type': 'tool_call', 'tool': tool, 'params': {'query': query}}, ensure_ascii=False)}\n\n"
            result = tool_case_retrieval(query)
            tool_results["case"] = result
            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool, 'summary': result.get('note', '0 条')}, ensure_ascii=False)}\n\n"
        else:
            # analyze / 其他步骤
            summary = analyze_step(query, tool_results)
            yield f"data: {json.dumps({'type': 'step_end', 'step': step.get('id'), 'summary': summary}, ensure_ascii=False)}\n\n"
    final_text = analyze_step(query, tool_results)
    yield f"data: {json.dumps({'type': 'final', 'answer': final_text}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"


@router.get("/actions")
def actions():
    return {"ok": True, "data": list_skills()}


@router.post("/assistant")
async def assistant(payload: dict):
    action = (payload.get("action") or "").strip()
    query = (payload.get("query") or "").strip()
    if not action:
        return {"ok": False, "error": {"code": "empty_action", "message": "action 不能为空"}}
    if not query:
        return {"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}
    return StreamingResponse(assistant_event_gen(action, query), media_type="text/event-stream")
