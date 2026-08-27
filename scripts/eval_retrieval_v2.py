"""检索链路评估脚本（qrels_v2，chunker_v2 child 级）。

用法:
  .venv/Scripts/python scripts/eval_retrieval_v2.py --limit 20
  .venv/Scripts/python scripts/eval_retrieval_v2.py --queries QA_dataset/法律/queries.json \
      --qrels data/QA_dataset/法律/qrels_v2.json --output data/QA_dataset/法律/eval_retrieval_v2.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.retrieval_service import get_retrieval_service


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rel_of(chunk_id: str, qrels: dict[str, int]) -> int:
    return qrels.get(chunk_id, 0)


def dcg(rels: list[int], k: int) -> float:
    d = 0.0
    for i, rel in enumerate(rels[:k]):
        d += rel / (__import__("math").log2(i + 2))
    return d


def ndcg(rels: list[int], k: int, ideal_rels: list[int]) -> float:
    ideal = sorted(ideal_rels, reverse=True)
    idcg = dcg(ideal, k)
    return dcg(rels, k) / idcg if idcg > 0 else 0.0


def metrics_for_query(ranked_chunks: list[str], qrels: dict[str, int], top_k: int) -> dict:
    rels = [qrels.get(c, 0) for c in ranked_chunks[:top_k]]
    all_rel = sorted(qrels.values(), reverse=True)
    total_rel = sum(1 for v in qrels.values() if v > 0)

    hit = 1 if any(r > 0 for r in rels) else 0
    # MRR：第一个相关结果的倒数（binary）
    mrr = 0.0
    for i, r in enumerate(rels, start=1):
        if r > 0:
            mrr = 1.0 / i
            break
    prec = sum(1 for r in rels if r > 0) / len(rels) if rels else 0.0
    recall = sum(1 for r in rels if r > 0) / total_rel if total_rel else 0.0
    return {
        f"hit@{top_k}": hit,
        f"mrr@{top_k}": mrr,
        f"p@{top_k}": prec,
        f"recall@{top_k}": recall,
        f"ndcg@{top_k}": ndcg(rels, top_k, all_rel),
    }


def main():
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--queries", default=str(root / "QA_dataset" / "法律" / "queries.json"))
    ap.add_argument("--qrels", default=str(root / "data" / "QA_dataset" / "法律" / "qrels_v2.json"))
    ap.add_argument("--output", default=str(root / "data" / "QA_dataset" / "法律" / "eval_retrieval_v2.json"))
    ap.add_argument("--limit", type=int, default=0, help="只评估前 N 个 query（0=全部）")
    ap.add_argument("--k-values", default="5,10", help="固定 K 指标，逗号分隔（基于 RRF 原始 top-10）")
    args = ap.parse_args()

    queries = load_json(args.queries)
    qrels_all = load_json(args.qrels)
    qid_to_query = {q["query_id"]: q for q in queries}

    qrels_by_query: dict[str, dict[str, int]] = defaultdict(dict)
    for r in qrels_all:
        if r.get("relevance", 0) > 0:
            qrels_by_query[r["query_id"]][r["chunk_id"]] = int(r["relevance"])

    qids = [qid for qid in qid_to_query if qid in qrels_by_query]
    if args.limit:
        qids = qids[: args.limit]
    k_values = [int(x) for x in args.k_values.split(",") if x.strip().isdigit()]

    print(f"queries 总数 {len(queries)}；有 qrels 的 query {len(qids)}（评估范围）", flush=True)
    svc = get_retrieval_service()
    svc._get_store()  # 预加载，避免统计进首条耗时

    agg: dict[str, list] = defaultdict(list)
    difficulty_agg: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    per_query = []
    errors = []
    t0 = time.time()
    for i, qid in enumerate(qids, 1):
        query_text = qid_to_query[qid]["query"]
        difficulty = qid_to_query[qid].get("difficulty", "unknown")
        try:
            out = svc.search(query_text)
            rrf_raw = [r["chunk_id"] for r in out.trace.get("rrf_raw_topk", [])]
            final_chunks = [r["chunk_id"] for r in out.trace.get("final_topk", [])]
        except Exception as e:
            errors.append({"query_id": qid, "error": str(e)})
            rrf_raw = []
            final_chunks = []

        q_metrics = {"query_id": qid, "query": query_text, "difficulty": difficulty,
                     "rrf_raw_chunks": rrf_raw[:10], "final_chunks": final_chunks[:10]}
        for k in k_values:
            m = metrics_for_query(rrf_raw, qrels_by_query[qid], k)
            q_metrics[f"fixed_k{k}"] = m
            for key, val in m.items():
                agg[key].append(val)
                difficulty_agg[difficulty][key].append(val)
        # 链路最终 topk 指标（难度自适应截断）
        final_k = len(final_chunks)
        m = metrics_for_query(final_chunks, qrels_by_query[qid], final_k)
        q_metrics["chain_final"] = {"final_k": final_k, **m}
        for key, val in m.items():
            key2 = "chain_" + key
            agg[key2].append(val)
            difficulty_agg[difficulty][key2].append(val)

        per_query.append(q_metrics)
        if i % 100 == 0:
            elapsed = time.time() - t0
            qps = i / elapsed if elapsed else 0
            print(f"  {i}/{len(qids)} 完成，耗时 {elapsed:.0f}s，速度 {qps:.2f} q/s", flush=True)

    elapsed = time.time() - t0
    summary = {
        "config": {
            "queries": args.queries,
            "qrels": args.qrels,
            "evaluated_queries": len(qids),
            "k_values": k_values,
            "elapsed_sec": round(elapsed, 1),
            "qps": round(len(qids) / elapsed, 2) if elapsed else 0,
        },
        "overall": {k: round(sum(vv) / len(vv), 4) if vv else None for k, vv in agg.items()},
        "by_difficulty": {
            d: {k: round(sum(vv) / len(vv), 4) if vv else None for k, vv in m.items()}
            for d, m in sorted(difficulty_agg.items())
        },
        "errors": errors,
    }
    summary["overall"] = {k: summary["overall"][k] for k in sorted(summary["overall"])}
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_query": per_query}, f, ensure_ascii=False, indent=2)
    print(f"评估完成，结果写入 {out_path}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
