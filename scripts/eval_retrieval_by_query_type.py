"""按 query_type 汇总检索评估指标（基于 eval_retrieval_v2_fixed.json）。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "QA_dataset" / "法律" / "eval_retrieval_v2_fixed.json"
QUERIES = ROOT / "QA_dataset" / "法律" / "queries.json"
OUT = ROOT / "data" / "QA_dataset" / "法律" / "eval_retrieval_by_query_type.md"


def main():
    eval_data = json.loads(EVAL.read_text(encoding="utf-8"))
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    qid_type = {q["query_id"]: q.get("query_type", ["unknown"])[0] for q in queries}
    per_query = eval_data["per_query"]

    metrics_by_type = defaultdict(lambda: defaultdict(list))
    counts = defaultdict(int)
    for pq in per_query:
        qid = pq["query_id"]
        t = qid_type.get(qid, "unknown")
        counts[t] += 1
        for key in ["hit@5", "hit@10", "mrr@5", "mrr@10", "p@5", "p@10", "recall@5", "recall@10", "ndcg@5", "ndcg@10"]:
            v = pq.get("fixed_k5", {}).get(key) if key.endswith("@5") else pq.get("fixed_k10", {}).get(key)
            if v is not None:
                metrics_by_type[t][key].append(v)

    lines = ["# 检索评估分 query_type 基线", "",
             "数据：`data/QA_dataset/法律/eval_retrieval_v2_fixed.json`（chunker_v2 child / qrels_v2）", "",
             "| query_type | 数量 | Hit@5 | Hit@10 | MRR@10 | P@5 | Recall@5 | Recall@10 | NDCG@10 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in sorted(counts, key=lambda x: -counts[x]):
        m = metrics_by_type[t]
        def avg(k):
            vv = m.get(k, [])
            return f"{sum(vv)/len(vv):.4f}" if vv else "-"
        lines.append(f"| {t} | {counts[t]} | {avg('hit@5')} | {avg('hit@10')} | {avg('mrr@10')} | {avg('p@5')} | {avg('recall@5')} | {avg('recall@10')} | {avg('ndcg@10')} |")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
