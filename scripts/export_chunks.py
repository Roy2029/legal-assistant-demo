"""导出新索引 chunks 为 JSONL（供新 qrels 设计）。只导出 child 级，带 total 保护。"""
import json
from pathlib import Path

from qdrant_client import QdrantClient

INDEX = Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")
OUT = Path("D:/个人/legal-assistant-demo/data/indices/法律/chunks_v2.jsonl")


def main():
    c = QdrantClient(path=str(INDEX))
    total = c.count(collection_name="chunks", exact=True).count
    print(f"collection 总点数 {total}")
    seen = 0
    offset = None
    rounds = 0
    with open(OUT, "w", encoding="utf-8") as f:
        while seen < total and rounds < 1000:
            rounds += 1
            pts, nxt = c.scroll(collection_name="chunks", limit=500, offset=offset, with_payload=True, with_vectors=False)
            if not pts:
                break
            for p in pts:
                pl = p.payload or {}
                if pl.get("chunk_level") != "child":
                    continue
                meta = pl.get("metadata", {})
                rec = {
                    "chunk_id": pl.get("chunk_id"),
                    "doc_id": pl.get("doc_id"),
                    "text": pl.get("text"),
                    "chunk_level": pl.get("chunk_level"),
                    "parent_chunk_id": pl.get("parent_chunk_id"),
                    "law_name": meta.get("law_name"),
                    "article_no": meta.get("article_no"),
                    "articles": meta.get("articles"),
                    "corpus": meta.get("corpus"),
                    "doc_type": meta.get("doc_type"),
                    "heading_path": pl.get("heading_path"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            seen += len(pts)
            if nxt is None:
                break
            offset = nxt
    c.close()
    size = OUT.stat().st_size / 1024 / 1024
    print(f"已导出 {seen} 点（其中 child 写入 {OUT}，{size:.1f} MB）")


if __name__ == "__main__":
    main()
