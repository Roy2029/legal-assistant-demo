"""导出新索引全部 chunks 为 JSONL，供新 qrels 数据集设计。"""
import json
import sys
from pathlib import Path

from qdrant_client import QdrantClient

INDEX = Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant")
OUT = Path("D:/个人/legal-assistant-demo/data/indices/法律/chunks_v2.jsonl")


def main():
    c = QdrantClient(path=str(INDEX))
    out = OUT
    total = 0
    offset = None
    with open(out, "w", encoding="utf-8") as f:
        while True:
            pts, nxt = c.scroll(collection_name="chunks", limit=500, offset=offset, with_payload=True, with_vectors=False)
            if not pts:
                break
            for p in pts:
                pl = p.payload or {}
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
                total += 1
            offset = nxt
    c.close()
    print(f"已导出 {total} 条 chunk 到 {OUT}")


if __name__ == "__main__":
    main()
