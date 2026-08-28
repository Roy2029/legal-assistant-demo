"""检索编排层（D09 §5）：并行执行多组子查询，fuse 融合或 separate 分组返回。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from offline_core.data_model import RetrievalResult
from online_core.retrieval_service import RetrievalOutput, RetrievalService


def _chunk_to_dict(r: RetrievalResult, from_query: str) -> dict:
    m = r.chunk.metadata or {}
    return {
        "chunk_id": r.chunk.chunk_id,
        "score": round(float(r.score), 4),
        "text": r.chunk.text[:400],
        "from_query": from_query,
        "meta": {
            "law_name": m.get("law_name", ""),
            "article_no": m.get("article_no", ""),
            "articles": m.get("articles") or [],
            "corpus": m.get("corpus", ""),
            "folder": m.get("folder", ""),
            "doc_type": m.get("doc_type", ""),
            "heading_path": r.chunk.heading_path or [],
        },
    }


def _rrf_fuse(results_by_query: dict[str, list[RetrievalResult]], top_k: int, k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievalResult] = {}
    origins: dict[str, list[str]] = {}
    for q, results in results_by_query.items():
        for rank, r in enumerate(results):
            cid = r.chunk.chunk_id
            chunks[cid] = r
            origins.setdefault(cid, []).append(q)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    out = []
    for cid, score in ranked:
        r = chunks[cid]
        d = _chunk_to_dict(r, ";".join(origins[cid]))
        d["score"] = round(score, 4)
        out.append(d)
    return out


def _run_query(svc: RetrievalService, query: str, folders: Optional[list[str]], top_k: int) -> RetrievalOutput:
    return svc.search(query, folders=folders)


def orchestrate(groups: list[dict], svc: Optional[RetrievalService] = None) -> dict:
    """执行一组检索计划。

    groups: [{group_id, merge_mode(fuse|separate), queries:[...], folders:[...], top_k:int}]
    """
    svc = svc or RetrievalService()
    group_results = []
    total_queries = 0
    errors = []

    for g in groups:
        queries = [q for q in (g.get("queries") or []) if q and q.strip()]
        if not queries:
            group_results.append({"group_id": g.get("group_id"), "merge_mode": g.get("merge_mode", "fuse"), "results": [], "error": "empty_queries"})
            continue
        merge_mode = g.get("merge_mode") or "fuse"
        folders = g.get("folders") or []
        top_k = int(g.get("top_k") or 8)
        total_queries += len(queries)

        results_by_query: dict[str, list[RetrievalResult]] = {}
        query_errors = []
        with ThreadPoolExecutor(max_workers=min(len(queries), 6)) as ex:
            futures = {ex.submit(_run_query, svc, q, folders, top_k): q for q in queries}
            for fut in as_completed(futures, timeout=90):
                q = futures[fut]
                try:
                    out = fut.result(timeout=20)
                    results_by_query[q] = list(out.results)
                except Exception as e:
                    query_errors.append({"query": q, "error": str(e)})
                    results_by_query[q] = []

        if merge_mode == "separate":
            results = []
            for q in queries:
                for r in results_by_query.get(q, []):
                    d = _chunk_to_dict(r, q)
                    results.append(d)
            group_results.append({"group_id": g.get("group_id"), "merge_mode": merge_mode, "results": results, "errors": query_errors})
        else:
            fused = _rrf_fuse(results_by_query, top_k)
            group_results.append({"group_id": g.get("group_id"), "merge_mode": "fuse", "results": fused, "errors": query_errors})
        errors.extend(query_errors)

    return {"groups": group_results, "stats": {"groups": len(groups), "queries": total_queries, "errors": errors}}
