"""知识库问答 API（D02 链路，SSE 流式）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .config_service import config_service
from .llm import LLMNotConfiguredError, llm_client
from online_core.retrieval_service import RetrievalService, RetrievalConfig
from online_core.citation_checker import CitationChecker

router = APIRouter(prefix="/api")

# 服务实例（懒加载）
_retrieval = None
_citation = None


def get_retrieval() -> RetrievalService:
    global _retrieval
    if _retrieval is None:
        _retrieval = RetrievalService(
            RetrievalConfig(index_path=str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")))
        )
    return _retrieval


def get_citation() -> CitationChecker:
    global _citation
    if _citation is None:
        _citation = CitationChecker()
    return _citation


def arabic_to_chinese(num: str) -> str:
    """阿拉伯数字条文号 → 中文数字（如 32 → 三十二）。"""
    digits = "零一二三四五六七八九"
    n = int(num)
    if n <= 10:
        return digits[n] if n < 10 else "十"
    if n < 20:
        return "十" + digits[n % 10]
    if n < 100:
        tens = n // 10
        ones = n % 10
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return num

def extract_article_snippet(text: str, article_no: str, max_chars: int = 600) -> str:
    """在 chunk 文本中定位第 article_no 条，截取该条附近片段。"""
    import re
    cn = arabic_to_chinese(article_no)
    m = re.search(r"第" + cn + r"条", text)
    if not m:
        return None
    start = m.start()
    next_m = re.search(r"第[一二三四五六七八九十百零千]+条", text[start + 1:])
    end = start + 1 + next_m.start() if next_m else min(len(text), start + max_chars)
    return text[max(0, start - 30):end].strip()

SYSTEM_PROMPT = """你是一名法律研究助手。只能依据提供的检索资料作答。
规则：
1. 引用法条必须使用资料中出现的名称与条文号，禁止编造；
2. 如果资料不足以回答，明确说明"检索资料不足"；
3. 输出末尾附引用列表，格式：法规名 第X条。
4. 不使用外部知识补充法条内容。"""

DISCLAIMER = "\n\n---\n本回答由 AI 生成，不构成正式法律意见，使用前须经执业律师核阅。"


def build_context(query: str) -> tuple[str, dict]:
    svc = get_retrieval()
    out = svc.search(query)
    target_article = None
    try:
        from online_core.query_parser import parse_query
        pq = parse_query(query)
        if pq.exact_match and isinstance(pq.article_no, str):
            target_article = pq.article_no
    except Exception:
        pass
    blocks = []
    seen = set()
    for r in out.results:
        m = r.chunk.metadata or {}
        text = r.chunk.text
        if target_article:
            snippet = extract_article_snippet(text, target_article)
            if snippet:
                key = snippet[:120]
                if key in seen:
                    continue
                seen.add(key)
                text = snippet
        src = f"{m.get('law_name', '未知法规')} 第{m.get('article_no', '?')}条"
        blocks.append(f"[来源：{src}]\n{text}")
    context = "\n\n".join(blocks) if blocks else "（检索无结果）"
    return context, out.trace

async def event_gen(query: str, session_id: str):
    # PreFilter 保守模式（D02 §3.2）
    from .prefilter import prefilter, TRIVIAL_REPLY
    pf = prefilter(query)
    if not pf["passed"]:
        yield f"data: {json.dumps({'type': 'prefilter_blocked', 'reason': pf['reason']}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'final', 'answer': TRIVIAL_REPLY}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        return
    trace_id = uuid.uuid4().hex
    yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id, 'trace_id': trace_id}, ensure_ascii=False)}\n\n"

    # 1. 检索
    yield f"data: {json.dumps({'type': 'step_start', 'step': 'retrieval'}, ensure_ascii=False)}\n\n"
    context, trace = build_context(query)
    yield f"data: {json.dumps({'type': 'trace', 'trace': trace}, ensure_ascii=False)}\n\n"


    # 脱敏：送 LLM 上下文与 query 脱敏（可逆假名化，D07）
    from .desensitize import desensitize, restore
    masked_context, _ = desensitize(context)
    masked_query, _ = desensitize(query)
    if not llm_client.configured:
        yield f"data: {json.dumps({'type': 'error', 'code': 'llm_not_configured', 'message': 'LLM 未配置，请在设置页填写 Base URL / API Key / Model'}, ensure_ascii=False)}\n\n"
        return

    # 2. 生成（首次）
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"问题：{masked_query}\n\n检索资料：\n{masked_context}"},
    ]
    answer = ""
    yield f"data: {json.dumps({'type': 'step_start', 'step': 'generation'}, ensure_ascii=False)}\n\n"
    async for piece in llm_client.stream_chat(messages):
        answer += piece
        yield f"data: {json.dumps({'type': 'llm_token', 'token': piece}, ensure_ascii=False)}\n\n"

    # 3. 引用校验（打回 1 次）
    checker = get_citation()
    result = checker.verify(answer)
    if result.unverifiable:
        # 打回重写
        feedback = "以下引用未能验证，请修正或删除：\n" + "\n".join(
            f"- {c.raw}" for c in result.unverifiable
        )
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": feedback + "\n请重新输出完整回答。"})
        answer2 = ""
        yield f"data: {json.dumps({'type': 'citation_check', 'rewrites': 1, 'unverifiable': [c.raw for c in result.unverifiable]}, ensure_ascii=False)}\n\n"
        async for piece in llm_client.stream_chat(messages):
            answer2 += piece
            yield f"data: {json.dumps({'type': 'llm_token', 'token': piece}, ensure_ascii=False)}\n\n"
        answer = answer2
        result = checker.verify(answer)

    # 4. 追加未验证风险提示
    suffix = ""
    if result.unverifiable:
        suffix = "\n\n⚠️ 以下引用未能验证，请核实：\n" + "\n".join(f"- {c.raw}" for c in result.unverifiable)
        yield f"data: {json.dumps({'type': 'citation_check', 'unverifiable': [c.raw for c in result.unverifiable]}, ensure_ascii=False)}\n\n"

    final = restore(answer + suffix) + DISCLAIMER
    yield f"data: {json.dumps({'type': 'final', 'answer': final}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(payload: dict):
    query = (payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": {"code": "empty_query", "message": "query 不能为空"}}
    session_id = payload.get("session_id") or uuid.uuid4().hex
    return StreamingResponse(event_gen(query, session_id), media_type="text/event-stream")
