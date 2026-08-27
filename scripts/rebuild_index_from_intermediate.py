"""从 chunker_v2 中间结果重建 qdrant 索引（children 作为唯一检索单元）。

背景
----
qrels_v2.json 的检索单元是 chunker_v2 的 child 块（chunk_id 只引用 child）。
因此重建索引只嵌入 child（中间结果 20018 条记录，按 chunk_id 去重后 17598 个
唯一块——相同法律文本跨文档复现时 chunk_id 内容哈希一致，qdrant 按 chunk_id
哈希为 point_id，同 ID 只能存一个 point，故去重保首现）。

用法
----
  .venv/Scripts/python scripts/rebuild_index_from_intermediate.py
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_core.data_model import Chunk
from offline_core.embedder import Embedder, HuggingFaceEmbeddingModel
from offline_core.store import QdrantConfig, QdrantStore


def load_intermediate_children(path: Path) -> list[dict]:
    """读取中间结果，仅保留 child，按 chunk_id 去重（保首现）。"""
    children = []
    seen: set[str] = set()
    n_dup = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json_loads(line)
            if d.get("chunk_level") != "child":
                continue
            cid = d["chunk_id"]
            if cid in seen:
                n_dup += 1
                continue
            seen.add(cid)
            children.append(d)
    print(f"中间结果 child 记录去重前（去重 {n_dup} 条重复 chunk_id）→ 唯一 {len(children)} 块")
    return children


def json_loads(line: str) -> dict:
    import json
    return json.loads(line)


def to_chunk(d: dict) -> Chunk:
    """将中间结果 dict 映射为 Chunk（block_ids 无来源置空，额外字段放 metadata）。"""
    metadata = {
        "law_name": d.get("law_name"),
        "article_no": d.get("article_no"),
        "articles": d.get("articles"),
        "doc_type": d.get("doc_type"),
    }
    return Chunk(
        chunk_id=d["chunk_id"],
        doc_id=d["doc_id"],
        text=d["text"],
        metadata=metadata,
        block_ids=[],
        heading_path=d.get("heading_path") or [],
        order=d.get("order", 0),
        token_count=d.get("token_count"),
        chunk_level="child",
        parent_chunk_id=d.get("parent_chunk_id"),
        child_chunk_ids=[],
        prev_chunk_id=d.get("prev_chunk_id"),
        next_chunk_id=d.get("next_chunk_id"),
    )


def main():
    ap = argparse.ArgumentParser(description="从 chunker_v2 中间结果重建索引")
    root = Path(__file__).resolve().parents[1]
    ap.add_argument(
        "--chunks",
        default=str(root / "data" / "indices" / "法律" / "chunk_v2_intermediate" / "chunks.jsonl"),
        help="chunker_v2 中间结果 chunks.jsonl 路径",
    )
    ap.add_argument(
        "--output",
        default=str(root / "data" / "indices" / "法律"),
        help="索引输出目录（内含 qdrant/）",
    )
    ap.add_argument("--model", default="D:/个人/Research/RAG1.0/local_model/bge-base-zh")
    ap.add_argument("--device", default="cuda", help="cuda / cpu（默认 cuda，不可用自动回退 cpu）")
    ap.add_argument("--batch-size", type=int, default=64, help="embedding batch size")
    args = ap.parse_args()

    chunks_path = Path(args.chunks)
    if not chunks_path.exists():
        print(f"中间结果不存在: {chunks_path}")
        sys.exit(1)
    output_dir = Path(args.output)
    qdrant_path = output_dir / "qdrant"

    # 关键：索引期加载内置法律词典，保证 sparse 分词与查询期一致
    from online_core.lexicon_service import load_builtin_lexicon
    n_lex = load_builtin_lexicon()
    print(f"内置法律词典已加载 {n_lex} 词（索引期分词）")

    device = args.device
    if device == "cuda":
        import torch
        if not torch.cuda.is_available():
            print("[warn] CUDA 不可用，回退 CPU")
            device = "cpu"
    embedding = HuggingFaceEmbeddingModel(model_name=args.model, device=device)
    embedder = Embedder(model=embedding, cache=None, batch_size=args.batch_size)

    children = load_intermediate_children(chunks_path)
    if not children:
        print("无 child 块，退出")
        sys.exit(1)

    chunks = [to_chunk(d) for d in children]

    # ── 备份旧索引（非破坏，可随时回滚）──
    if qdrant_path.exists():
        backup = qdrant_path.parent / f"qdrant_old_{time.strftime('%Y%m%d_%H%M%S')}"
        print(f"备份旧索引 → {backup}")
        shutil.move(str(qdrant_path), str(backup))

    records = embedder.embed_chunks(chunks)
    print(f"生成 {len(records)} 条向量记录（batch={args.batch_size}）")

    qdrant_path.mkdir(parents=True, exist_ok=True)
    cfg = QdrantConfig(
        mode="embedded",
        path=str(qdrant_path),
        collection_name="chunks",
        dense_dimension=embedding.dimension,
        dense_on_disk=True,
        enable_sparse=True,
    )
    store = QdrantStore(cfg)
    store.upsert(records)
    store.save(str(qdrant_path))
    print(f"索引已写入 {qdrant_path}")

    # 写入 manifest 便于评估脚本读取模型/维度
    import json
    manifest = {
        "embedding_model": args.model,
        "embedding_dimension": embedding.dimension,
        "n_chunks": len(records),
        "chunk_level": "child",
        "collection": "chunks",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_intermediate": str(chunks_path),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest 已写入 {manifest_path}")
    print(f"索引完成: {len(records)} child 块")


if __name__ == "__main__":
    main()
