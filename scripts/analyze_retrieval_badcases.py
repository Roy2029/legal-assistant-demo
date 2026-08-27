"""检索 badcase 归因：分析 hit@10=0 的 query（基于 eval_retrieval_v2_fixed.json）。

不需要 Qdrant：chunk 文本/元数据来自 chunk_v2_intermediate/chunks.jsonl。
输出：
- data/QA_dataset/法律/retrieval_badcases.json
- data/QA_dataset/法律/retrieval_badcases.md
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.query_parser import parse_query

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "QA_dataset" / "法律" / "eval_retrieval_v2_fixed.json"
QUERIES = ROOT / "QA_dataset" / "法律" / "queries.json"
QRELS = ROOT / "data" / "QA_dataset" / "法律" / "qrels_v2.json"
CHUNKS = ROOT / "data" / "indices" / "法律" / "chunk_v2_intermediate" / "chunks.jsonl"
OUT_JSON = ROOT / "data" / "QA_dataset" / "法律" / "retrieval_badcases.json"
OUT_MD = ROOT / "data" / "QA_dataset" / "法律" / "retrieval_badcases.md"


def load_chunks():
    chunks = {}
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            if c.get("chunk_level") == "child":
                chunks[c["chunk_id"]] = c
    return chunks


def snip(text, n=140):
    t = (text or "").replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def main():
    eval_data = json.loads(EVAL.read_text(encoding="utf-8"))
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    qrels = json.loads(QRELS.read_text(encoding="utf-8"))
    qid_info = {q["query_id"]: q for q in queries}
    qrels_by_qid = defaultdict(list)
    for r in qrels:
        if r.get("relevance", 0) > 0:
            qrels_by_qid[r["query_id"]].append(r)
    chunks = load_chunks()

    bad = [p for p in eval_data["per_query"] if p.get("fixed_k10", {}).get("hit@10") == 0]
    print(f"hit@10=0 的 query 数：{len(bad)}")

    reasons = Counter()
    rows = []
    for p in bad:
        qid = p["query_id"]
        q = qid_info[qid]
        text = q["query"]
        pq = parse_query(text)
        expected = qrels_by_qid.get(qid, [])
        exp_chunks = []
        for r in expected[:8]:
            c = chunks.get(r["chunk_id"])
            exp_chunks.append({
                "chunk_id": r["chunk_id"],
                "relevance": r["relevance"],
                "law_name": c.get("law_name") if c else "",
                "articles": c.get("articles") if c else None,
                "text": snip(c.get("text") if c else "", 160),
            })
        got = []
        for cid in p.get("rrf_raw_chunks", [])[:10]:
            c = chunks.get(cid)
            got.append({
                "chunk_id": cid,
                "law_name": c.get("law_name") if c else "",
                "articles": c.get("articles") if c else None,
                "text": snip(c.get("text") if c else "", 120),
            })

        # 自动归类（优先级从高到低）
        reason = "unknown"
        m_law_in_query = re.search(r"《([^》]{2,40}?)》", text)
        query_law_names = [m.group(1) for m in re.finditer(r"《([^》]{2,40}?)》", text)]
        exp_law_names = [c["law_name"] for c in exp_chunks if c["law_name"]]

        if pq.exact_match and isinstance(pq.article_no, str):
            exp_articles = []
            for c in exp_chunks:
                exp_articles.extend(c.get("articles") or [])
            if exp_articles and pq.article_no not in exp_articles:
                reason = "article_filter_wrong_article"
            else:
                reason = "exact_match_but_ranking_miss"
        elif m_law_in_query and pq.law_name is None:
            # 查询里明确写了法规名，但 LAW_ALIASES 没识别出来 → 没有 law_name filter
            reason = "law_name_in_query_but_parser_miss"
        elif re.search(r"该法|这部法律|该决定|这份决定|本法|上述|前款", text) and pq.law_name is None:
            reason = "pronoun_anaphora_no_law_name"
        elif re.search(r"法律中|法律关于|法律对", text) and pq.law_name is None:
            reason = "generic_law_reference"
        elif pq.filter.get("effect_level"):
            reason = "effect_level_filter"
        elif exp_law_names and any(any(exp_name in c["law_name"] or c["law_name"] in exp_name for exp_name in exp_law_names) for c in got if c["law_name"]):
            # 实际结果里出现了期望法规，但命中的不是期望条文块
            reason = "right_law_wrong_article"
        elif query_law_names and exp_law_names and not any(any(qn in en or en in qn for en in exp_law_names) for qn in query_law_names):
            reason = "qrels_mapping_suspect"
        elif q.get("query_type") and q.get("query_type")[0] in ("multi_hop", "procedural"):
            reason = "multi_hop_procedural"
        else:
            reason = "ranking_miss"
        reasons[reason] += 1

        rows.append({
            "query_id": qid,
            "query": text,
            "query_type": q.get("query_type", [""])[0],
            "difficulty": q.get("difficulty"),
            "parsed": {
                "law_name": pq.law_name,
                "article_no": pq.article_no,
                "effect_level": pq.effect_level,
                "filter": pq.filter,
                "exact_match": pq.exact_match,
                "excluded": pq.excluded,
            },
            "reason": reason,
            "expected_chunks": exp_chunks,
            "retrieved_top10": got,
        })

    rows.sort(key=lambda r: r["reason"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"total": len(rows), "reason_dist": dict(reasons), "badcases": rows}, f, ensure_ascii=False, indent=2)

    lines = ["# 检索 badcase 归因（Hit@10=0）", "",
             f"共 {len(rows)} 条。归因分布：", ""]
    for reason, cnt in reasons.most_common():
        lines.append(f"- {reason}: {cnt}")
    lines.append("")
    for r in rows:
        lines.append(f"## {r['query_id']}")
        lines.append(f"- query_type={r['query_type']} difficulty={r['difficulty']} reason={r['reason']}")
        lines.append(f"- query: {r['query']}")
        lines.append(f"- parsed: {json.dumps(r['parsed'], ensure_ascii=False)}")
        lines.append("- 期望命中：")
        for c in r["expected_chunks"][:5]:
            lines.append(f"  - [{c['law_name']} 条{c['articles']}] {c['text']}")
        lines.append("- 实际 top10：")
        for c in r["retrieved_top10"][:5]:
            lines.append(f"  - [{c['law_name']} 条{c['articles']}] {c['text']}")
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("reason dist:", dict(reasons))
    print(f"输出：{OUT_JSON} 和 {OUT_MD}")


if __name__ == "__main__":
    main()
