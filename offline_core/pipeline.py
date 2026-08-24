"""离线流程编排 — 文件夹批量处理 → 索引构建 → 持久化。

典型用法：
    config = PipelineConfig(folder_path="./docs", output_dir="data/indices")
    pipeline = Pipeline(config)
    pipeline.run(embedding_model=my_model)
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

from .modules import BaseEmbeddingModel, log_module
from .data_model import Chunk, StructuredDocument, EmbeddingRecord
from .manifest import compute_doc_id
from .parser import MarkdownParser, SimpleTextParser
from .pdf_parser import PdfParser
from .docx_parser import DocxParser, LegalDocxParser
from .chunker import ParentChildChunker, StructureAwareChunker
from .embedder import Embedder
from .enricher import (
    MetadataPipeline,
    SourceMetadataEnricher,
    StructureMetadataEnricher,
    KeywordEnricher,
    LanguageEnricher,
    CsvMetadataEnricher,
    _extract_bbbs,
)
from .store import QdrantStore, QdrantConfig
from .chunk_export import export_to_html


SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


@dataclass
class PipelineConfig:
    """离线流程配置。"""

    folder_path: str                       # 文档文件夹路径
    output_dir: str = "data/indices"         # 所有索引统一输出到此目录

    # 向量存储类型: "qdrant"(推荐) | "faiss"(已废弃)
    store_type: str = "qdrant"

    enable_vector_index: bool = True       # 是否构建向量索引
    enable_bm25_index: bool = True         # 是否构建 BM25 索引（Qdrant 下为 sparse vector）
    enable_metadata_enrich: bool = False   # 是否启用 metadata 丰富
    chunk_max_chars: int = 1000            # 分块最大字符数
    enable_chunk_export: bool = True      # 是否自动导出 HTML 浏览页面
    enable_parent_child: bool = False     # 是否启用 Parent-Child 切块（替代普通切块）

    # Qdrant 索引参数（store_type="qdrant" 时生效）
    qdrant_on_disk: bool = True
    qdrant_quantization: str | None = None  # "scalar" | "product" | "binary" | None
    qdrant_indexing_threshold: int = 20_000
    qdrant_subdir: str = "qdrant"          # Qdrant 子目录名（默认 "qdrant"，多模型时如 "qdrant_bge-m3"）
    embed_batch_size: int = 32             # 嵌入阶段 batch size（大模型需调小以免 OOM/显存颠簸）

    # CSV 元数据关联（为 None 时不加载 CsvMetadataEnricher）
    metadata_csv_path: str | None = None


class Pipeline:
    """离线流程编排器。

    负责：扫描文件夹 → 解析 → 分块 → (可选元数据丰富) → 嵌入 → 索引构建 → 持久化。
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._md_parser = MarkdownParser()
        self._txt_parser = SimpleTextParser()
        self._pdf_parser = PdfParser(extract_tables=True, extract_images=False)
        self._docx_parser = DocxParser(extract_tables=True, extract_images=False)
        self._legal_docx_parser = LegalDocxParser(extract_tables=True, extract_images=False)
        self._chunker = StructureAwareChunker(max_chars=config.chunk_max_chars)
        self._pc_chunker = ParentChildChunker()

    # ── 文件发现 ──────────────────────────────────────────────

    def _collect_files(self) -> list[Path]:
        root = Path(self.config.folder_path)
        if not root.exists():
            raise FileNotFoundError(f"文件夹不存在: {root}")
        files = []
        for p in root.rglob("*"):
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file():
                files.append(p)
        files.sort(key=lambda p: str(p))
        return files

    # ── 解析 ──────────────────────────────────────────────────

    def _parse(self, file_path: Path) -> Optional[StructuredDocument]:
        ext = file_path.suffix.lower()
        try:
            if ext == ".md":
                return self._md_parser.parse(str(file_path))
            elif ext == ".txt":
                return self._txt_parser.parse(str(file_path))
            elif ext == ".pdf":
                return self._pdf_parser.parse(str(file_path))
            elif ext == ".docx":
                if self._legal_docx_parser.detect(str(file_path)):
                    return self._legal_docx_parser.parse(str(file_path))
                return self._docx_parser.parse(str(file_path))
            else:
                return None
        except Exception as e:
            print(f"  [跳过] 解析失败 {file_path.name}: {e}")
            return None

    # ── 元数据丰富 ────────────────────────────────────────────

    def _build_enricher(self) -> MetadataPipeline:
        enrichers = [
            SourceMetadataEnricher(),
            StructureMetadataEnricher(),
            KeywordEnricher(top_k=5),
            LanguageEnricher(),
        ]
        if self.config.metadata_csv_path:
            enrichers.insert(0, CsvMetadataEnricher(self.config.metadata_csv_path))
        return MetadataPipeline(enrichers)

    # ── 摘要 Collection ───────────────────────────────────────

    def _build_summary_records(
        self,
        docs: dict[str, StructuredDocument],
        chunks: list[Chunk],
        embedding_model: BaseEmbeddingModel,
    ) -> list[EmbeddingRecord]:
        """为每个文档生成摘要点。

        摘要文本 = heading_path 拼接 + 首段内容（前 500 字符）
        与常规 chunk 写入同一 Collection，chunk_level="document"。
        """
        # 按 doc_id 分组 chunks
        doc_chunks: dict[str, list[Chunk]] = {}
        for c in chunks:
            doc_chunks.setdefault(c.doc_id, []).append(c)

        summary_records: list[EmbeddingRecord] = []
        for doc_id, doc in docs.items():
            doc_chunk_list = doc_chunks.get(doc_id, [])
            if not doc_chunk_list:
                continue

            # 按 order 排序
            doc_chunk_list.sort(key=lambda c: c.order)

            # P-C 模式下过滤掉 child 级别（太细粒度不适合做摘要）
            summary_candidates = [c for c in doc_chunk_list if c.chunk_level != "child"]
            if summary_candidates:
                doc_chunk_list = summary_candidates

            # 1. heading_path 拼接作为结构描述
            if doc_chunk_list[0].heading_path:
                heading_text = " > ".join(doc_chunk_list[0].heading_path)
            else:
                heading_text = doc.metadata.get("file_name", "")

            # 2. 首段有效内容（截取前 500 字符）
            first_text = doc_chunk_list[0].text[:500]

            # 3. 拼接摘要文本
            summary_text = f"{heading_text}\n\n{first_text}"

            # 4. 生成 embedding
            [summary_vector] = embedding_model.embed_texts([summary_text])

            # 5. 构造摘要 EmbeddingRecord
            summary_chunk = Chunk(
                chunk_id=f"{doc_id}_document",
                doc_id=doc_id,
                text=summary_text,
                metadata=dict(doc_chunk_list[0].metadata),  # 继承第一个 chunk 的 metadata
                block_ids=[],
                heading_path=doc_chunk_list[0].heading_path,
                order=-1,
            )
            summary_record = EmbeddingRecord(
                chunk=summary_chunk,
                vector=summary_vector,
                embedding_model=embedding_model.model_name,
                dimension=embedding_model.dimension,
                content_hash=str(hash(summary_text)),
                vector_type="dense",
            )
            summary_records.append(summary_record)

        return summary_records

    # ── KB 词表保存 ───────────────────────────────────────────

    def _save_kb_vocab(self, store: QdrantStore, output_dir: str) -> None:
        """从 BM25Encoder 的 vocabulary 提取 KB 核心词表并保存。"""
        encoder = store.bm25_encoder
        if encoder is None:
            return
        try:
            # 从 term_to_id 提取所有 term（后续可优化：只保留 DF≥2 的 term）
            vocab = list(encoder._term_to_id.keys())
            output_path = Path(output_dir) / "kb_vocab.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(vocab, f, ensure_ascii=False)
            logger.info(f"KB 词表已保存: {output_path} ({len(vocab)} 个词)")
        except Exception as e:
            logger.warning(f"KB 词表保存失败: {e}")

    # ── 入口 ──────────────────────────────────────────────────

    @log_module("离线流程")
    def run(self, embedding_model: Optional[BaseEmbeddingModel] = None) -> dict:
        config = self.config

        # 1. 收集文件
        files = self._collect_files()
        if not files:
            print(f"在 {config.folder_path} 中未找到支持的文档 ({', '.join(SUPPORTED_EXTENSIONS)})")
            return
        print(f"发现 {len(files)} 个文档")

        # 2. 逐文件解析 → 分块（含可选的 enrich）
        all_chunks: list[Chunk] = []
        all_docs: dict[str, StructuredDocument] = {}  # doc_id → document（用于后续摘要生成）
        enricher = self._build_enricher() if config.enable_metadata_enrich else None


        for file_path in files:
            doc = self._parse(file_path)
            if doc is None:
                continue
            # 覆盖为确定性 doc_id（替代解析器内部的 uuid4）
            content_bytes = file_path.read_bytes()
            doc.doc_id = compute_doc_id(content_bytes)

            # 从文件名提取 bbbs 标识符（用于 CSV 元数据关联）
            bbbs = _extract_bbbs(str(file_path))
            if bbbs:
                doc.metadata["bbbs"] = bbbs

            # 分块（P-C 模式 vs 普通结构切块）
            if config.enable_parent_child:
                pc_result = self._pc_chunker.build(doc)
                # parent + child 合并写入同一 Collection，chunk_level 区分层级
                chunks = pc_result.parent_chunks + pc_result.child_chunks
            else:
                chunks = self._chunker.chunk(doc)

            if enricher:
                chunks = enricher.run(chunks, doc)
            all_chunks.extend(chunks)
            all_docs[doc.doc_id] = doc
            print(f"  ✓ {file_path.name}  → {len(chunks)} chunks")

        if not all_chunks:
            print("没有生成任何 chunk，流程结束")
            return {"total_documents": len(all_docs), "total_chunks": 0}
        print(f"共生成 {len(all_chunks)} 个 chunk")

        # 3. 构建向量索引（Qdrant 或 FAISS）
        if config.enable_vector_index:
            if embedding_model is None:
                raise ValueError("启用向量索引时必须提供 embedding_model")
            embedder = Embedder(model=embedding_model, cache=None, batch_size=config.embed_batch_size)
            records = embedder.embed_chunks(all_chunks)
            print(f"  生成 {len(records)} 条向量记录")

            if config.store_type == "qdrant":
                # Qdrant 统一存储（dense + sparse 同一 Collection）
                db_path = Path(config.output_dir) / config.qdrant_subdir
                qdrant_config = QdrantConfig(
                    mode="embedded",
                    path=str(db_path),
                    collection_name="chunks",
                    dense_dimension=embedding_model.dimension,
                    dense_on_disk=config.qdrant_on_disk,
                    dense_quantization=config.qdrant_quantization,
                    dense_indexing_threshold=config.qdrant_indexing_threshold,
                    enable_sparse=config.enable_bm25_index,
                )
                qdrant_store = QdrantStore(qdrant_config)
                qdrant_store.upsert(records)

                # 生成摘要点（摘要 Collection：同一 Collection，chunk_level="document"）
                if all_docs:
                    summary_records = self._build_summary_records(
                        all_docs, all_chunks, embedding_model
                    )
                    for sr in summary_records:
                        qdrant_store.upsert_summary(sr)
                    if summary_records:
                        print(f"  生成 {len(summary_records)} 条文档摘要点")

                # 如果同时启用 BM25（sparse），由 QdrantStore 统一管理
                if config.enable_bm25_index:
                    qdrant_store.save(str(db_path))
                    print(f"  Qdrant 索引（dense + sparse）已保存到 {db_path}")
                else:
                    qdrant_store.save(str(db_path))
                    print(f"  Qdrant 索引（dense）已保存到 {db_path}")

                # 保存 KB 核心词表（用于 PreFilter 的 KB 无关检测）
                self._save_kb_vocab(qdrant_store, config.output_dir)
            else:
                # 旧版 FAISS
                from .store import FAISSStore
                store = FAISSStore(dimension=embedding_model.dimension)
                store.upsert(records)
                save_path = str(Path(config.output_dir) / "faiss")
                store.save(save_path)
                print(f"  FAISS 索引已保存到 {save_path}")

        # 4. 构建 BM25 索引（仅旧版 FAISS 模式需要独立 BM25Store）
        if config.enable_bm25_index and config.store_type != "qdrant":
            from .store import BM25Store
            store = BM25Store()
            store.upsert(all_chunks)
            save_path = str(Path(config.output_dir) / "bm25")
            store.save(save_path)
            print(f"  BM25 索引已保存到 {save_path}")

        # 5. 导出 Chunk HTML 浏览页面
        if config.enable_chunk_export:
            html_path = export_to_html(
                all_chunks,
                str(Path(config.output_dir) / "chunks.html"),
                title=f"Chunk 浏览 — {Path(config.folder_path).name}",
            )
            print(f"  Chunk HTML 已导出到 {html_path}")

        # 6. 持久化 StructuredDocument JSON 文件
        docs_dir = Path(config.output_dir) / "documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        saved_count = 0
        for doc_id, doc in all_docs.items():
            try:
                doc_path = docs_dir / f"{doc_id}.json"
                doc_path.write_text(
                    doc.model_dump_json(indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                saved_count += 1
            except Exception as e:
                logger.warning("文档 JSON 写入失败 [%s]: %s", doc_id, e)
        if saved_count:
            print(f"  {saved_count} 个文档 JSON 已保存到 {docs_dir}")

        print("离线流程完成")
        return {
            "total_documents": len(all_docs),
            "total_chunks": len(all_chunks),
        }
