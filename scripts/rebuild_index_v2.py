"""按 D01 目标分块策略重建公共法规库索引（chunker_v2）。

用法:
  .venv/Scripts/python scripts/rebuild_index_v2.py \
      --input "D:/github-repo/法律数据库爬虫/laws_files/法律" \
      --output "D:/个人/legal-assistant-demo/data/indices/法律" \
      --model "D:/个人/Research/RAG1.0/local_model/bge-base-zh" \
      --limit 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transformers import AutoTokenizer

from offline_core.docx_parser import LegalDocxParser
from offline_core.parser import MarkdownParser
from offline_core.chunker_v2 import LegalStructureChunker
from offline_core.data_model import StructuredDocument
from offline_core.embedder import Embedder, HuggingFaceEmbeddingModel
from offline_core.manifest import compute_doc_id
from offline_core.docx_parser import chinese_to_arabic
from offline_core.store import QdrantStore, QdrantConfig

SUPPORTED = {".docx", ".md", ".txt"}


def collect_files(input_dir: Path, limit: int):
    files = sorted([p for p in input_dir.rglob("*") if p.suffix.lower() in SUPPORTED and p.is_file()])
    return files[:limit] if limit else files


def extract_law_name(path: Path) -> str:
    """从文件名提取法规名（去掉 32 位 hex 前缀）。"""
    import re
    name = path.stem
    m = re.match(r"^[0-9a-f]{32}_(.+)$", name)
    return m.group(1) if m else name


def extract_first_article(doc) -> str | None:
    """从文档提取第一个条文号（阿拉伯数字）。"""
    import re
    for b in doc.blocks:
        if hasattr(b, "content"):
            m = re.match(r"^第([一二三四五六七八九十百零千]+)条", b.content.strip())
            if m:
                try:
                    return str(chinese_to_arabic(m.group(1)))
                except Exception:
                    return m.group(1)
    return None


def parse_file(path: Path):
    ext = path.suffix.lower()
    if ext == ".docx":
        parser = LegalDocxParser()
        try:
            if parser.detect(str(path)):
                return parser.parse(str(path))
        except Exception:
            pass
        # 降级 Markdown 不可行时返回 None
        return None
    if ext == ".md":
        return MarkdownParser().parse(str(path))
    if ext == ".txt":
        return MarkdownParser().parse(str(path))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", default="D:/个人/Research/RAG1.0/local_model/bge-base-zh")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    ap.add_argument("--tokenizer", default="D:/个人/Research/RAG1.0/local_model/bge-base-zh")
    ap.add_argument("--device", default="cuda", help="cuda / cpu（默认 cuda，不可用自动回退 cpu）")
    ap.add_argument("--batch-size", type=int, default=64, help="embedding batch size（GTX 1650 4GB 推荐 64）")
    args = ap.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.exists():
        print(f"输入目录不存在: {input_dir}")
        sys.exit(1)

    files = collect_files(input_dir, args.limit)
    print(f"待处理 {len(files)} 份文件")

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    chunker = LegalStructureChunker(tokenizer=tokenizer)

    device = args.device
    if device == "cuda":
        import torch
        if not torch.cuda.is_available():
            print("[warn] CUDA 不可用，回退 CPU")
            device = "cpu"
    embedding = HuggingFaceEmbeddingModel(model_name=args.model, device=device)
    embedder = Embedder(model=embedding, cache=None, batch_size=args.batch_size)

    all_chunks = []
    all_parents = []
    for fp in files:
        doc: StructuredDocument | None = parse_file(fp)
        if doc is None:
            print(f"  [跳过] {fp.name}")
            continue
        content = fp.read_bytes()
        doc.doc_id = compute_doc_id(content)
        law_name = extract_law_name(fp)
        meta = {"law_name": law_name, "doc_type": "law"}
        parents, children = chunker.chunk(doc, metadata_extra=meta)
        all_parents.extend(parents)
        all_chunks.extend(children)
        print(f"  ✓ {fp.name}: {len(parents)} parents / {len(children)} children")

    if not all_chunks:
        print("无 chunk 生成，退出")
        return

    # 合并 parents + children 一起嵌入入库（parent 也建 dense 向量便于完整召回）
    records = embedder.embed_chunks(all_parents + all_chunks)
    print(f"生成 {len(records)} 条向量记录")

    qdrant_path = output_dir / "qdrant"
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
    print(f"total parents={len(all_parents)}, children={len(all_chunks)}")


if __name__ == "__main__":
    main()
