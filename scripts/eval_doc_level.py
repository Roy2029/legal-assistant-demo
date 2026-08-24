"""文档级检索评估（D03 §4.1）：旧 qrels 聚合为 doc 级，评估新索引。

用法:
  .venv/Scripts/python scripts/eval_doc_level.py --limit 100
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAG1_ROOT = Path("D:/个人/Research/RAG1.0")
OLD_INDEX = RAG1_ROOT / "data/indices/法律/qdrant"
NEW_INDEX = Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")
QA_DIR = Path("D:/个人/legal-assistant-demo/QA_dataset/法律")


def build_old_chunk_doc_map() -> dict[str, str]:
    """旧索引: chunk_id -> doc_id。"""
    from qdrant_client import QdrantClient
    c = QdrantClient(path=str(OLD_INDEX))
    mapping = {}
    offset = None
    while True:
        pts, nxt = c.scroll(collection_name="chunks", limit=500, offset=offset, with_payload=True, with_vectors=False)
        if not pts:
            break
        for p in pts:
            payload = p.payload or {}
            cid = payload.get("chunk_id")
            did = payload.get("doc_id")
            if cid and did:
                mapping[cid] = did
        offset = nxt
    c.close()
    return mapping


def build_doc_qrels(qrels_path: Path, chunk_doc_map: dict[str, str]) -> dict[str, set[str]]:
    qrels = json.loads(qrels_path.read_text(encoding="utf-8"))
    doc_qrels = defaultdict(set)
    unmatched = 0
    for entry in qrels:
        qid = entry["query_id"]
        cid = entry["chunk_id"]
        did = chunk_doc_map.get(cid)
        if did:
            doc_qrels[qid].add(did)
        else:
            unmatched += 1
    print(f"qrels 条目 {len(qrels)}，聚合 {len(doc_qrels)} 个 query；未匹配 chunk {unmatched}")
    return doc_qrels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只评估前 N 个 query（0=全部）")
    ap.add_argument("--k", default="5,10,20")
    ap.add_argument("--old-index", default=str(OLD_INDEX))
    ap.add_argument("--new-index", default=str(NEW_INDEX))
    ap.add_argument("--queries", default=str(QA_DIR / "queries.json"))
    ap.add_argument("--qrels", default=str(QA_DIR / "qrels.json"))
    args = ap.parse_args()

    ks = [int(x) for x in args.k.split(",")]
    chunk_doc_map = build_old_chunk_doc_map()
    doc_qrels = build_doc_qrels(Path(args.qrels), chunk_doc_map)

    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    if args.limit:
        queries = queries[: args.limit]
    print(f"评估 {len(queries)} 个 query")

    from online_core.retrieval_service import RetrievalService, RetrievalConfig
    svc = RetrievalService(RetrievalConfig(index_path=args.new_index))

    hit_at_k = {k: 0 for k in ks}
    total = 0
    for i, q in enumerate(queries, 1):
        qid = q["query_id"]
        relevant = doc_qrels.get(qid, set())
        if not relevant:
            continue
        total += 1
        out = svc.search(q["query"])
        # 注意：service 已 rerank 到 top_k 较小，这里直接重跑 recall 空间
        from offline_core.retriever import HybridMethod
        # 为效率，直接取 service 内部 method 跑 recall_top_k
        # 简化：用 svc 的 method
        method = svc._get_method()
        raw = method.search(q["query"], top_k=50)
        # 取前 max(k) 的 doc
        got = []
        seen = set()
        for r in raw:
            d = r.chunk.doc_id
            if d not in seen:
                seen.add(d)
                got.append(d)
        for k in ks:
            if relevant & set(got[:k]):
                hit_at_k[k] += 1
        if i % 100 == 0:
            print(f"  {i}/{len(queries)} done")

    print(f"\n文档级评估结果（{total} 个有相关 doc 的 query）:")
    for k in ks:
        print(f"  Doc-Recall@{k} = {hit_at_k[k]/total:.4f}" if total else "  no data")


if __name__ == "__main__":
    main()
