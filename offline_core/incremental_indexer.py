"""IncrementalIndexer — 增量索引同步器。

职责：
  1. scan() → 扫描源文件夹，与 manifest 比对，输出变更集
  2. apply() → 执行变更（新增/修改/删除），更新 Qdrant + manifest
  3. sync()  → scan + apply + BM25 dirty 检查的一体化入口
  4. get_status() → 索引状态概览
  5. rebuild_full() → 全量重建
  6. rebuild_bm25() → 仅重算 BM25 sparse vectors

复用现有的 Pipeline 组件：解析器、分块器、嵌入器、QdrantStore。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chunker import ParentChildChunker, StructureAwareChunker
from .data_model import Chunk, EmbeddingRecord, StructuredDocument
from .docx_parser import DocxParser, LegalDocxParser
from .embedder import Embedder
from .manifest import (
    Bm25State,
    ChangeSet,
    FileEntry,
    Manifest,
    compute_chunk_id,
    compute_content_hash,
    compute_doc_id,
    scan,
)
from .modules import BaseEmbeddingModel
from .parser import MarkdownParser, SimpleTextParser
from .pdf_parser import PdfParser
from .store import BM25Encoder, QdrantConfig, QdrantStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


class IncrementalIndexer:
    """增量索引同步器。

    用法：
        indexer = IncrementalIndexer(config, "法律库", "data/sources/法律")
        report = indexer.sync()
        print(report)
    """

    def __init__(
        self,
        config,
        kb_name: str,
        source_folder: str,
    ):
        """
        Args:
            config: ConfigManager 实例（用于读取 index_store_dir、embedding 等配置）
            kb_name: 知识库名称（也是 Qdrant collection 名称）
            source_folder: 源文档文件夹路径
        """
        self.kb_name = kb_name
        self.source_folder = Path(source_folder)

        # 目录布局
        index_store = Path(config.get("index_store_dir", "data/indices"))
        self.index_dir = index_store / kb_name  # 存放 manifest.json
        self.qdrant_path = index_store / "qdrant"  # 共享嵌入式 DB

        # 嵌入模型配置
        self.embedding_model_name = config.get(
            "vector_db.dense.embedding_model", "local_model/bge-base-zh"
        )
        self.embedding_dim = config.get("vector_db.dense.dimension", 768)
        self.device = config.get("rerank.device", "cuda")

        # BM25 开关
        self.enable_sparse = config.get("vector_db.sparse.enabled", True)

        # 分块配置（复用 Pipeline 默认值）
        self.chunk_max_chars = 1000
        self.enable_parent_child = False  # 默认普通分块

        # ── 加载或创建 Manifest ──
        self.manifest: Manifest = self._load_or_create_manifest()

        # ── 延迟初始化组件 ──
        self._store: Optional[QdrantStore] = None
        self._embedding_model: Optional[BaseEmbeddingModel] = None
        self._embedder: Optional[Embedder] = None

        # ── 解析器（轻量，直接创建） ──
        self._md_parser = MarkdownParser()
        self._txt_parser = SimpleTextParser()
        self._pdf_parser = PdfParser(extract_tables=True, extract_images=False)
        self._docx_parser = DocxParser(extract_tables=True, extract_images=False)
        self._legal_docx_parser = LegalDocxParser(
            extract_tables=True, extract_images=False
        )

        # ── 分块器 ──
        self._chunker = StructureAwareChunker(max_chars=self.chunk_max_chars)
        self._pc_chunker = ParentChildChunker()

    # ── 内部初始化 ────────────────────────────────────────────────────

    def _load_or_create_manifest(self) -> Manifest:
        """加载已有 manifest，不存在则创建空框架。"""
        manifest_path = self.manifest_path
        if manifest_path.exists():
            try:
                return Manifest.load(manifest_path)
            except Exception as e:
                logger.warning("Manifest 损坏，将重建: %s", e)
                return Manifest.create(
                    kb_name=self.kb_name,
                    source_folder=str(self.source_folder),
                )
        # 空框架，不设 source_folder——首次 sync 时需要用户提供
        return Manifest.create(
            kb_name=self.kb_name,
            source_folder=str(self.source_folder),
        )

    @property
    def manifest_path(self) -> Path:
        """manifest.json 完整路径。"""
        return self.index_dir / "manifest.json"

    # ── Lazy 资源 ────────────────────────────────────────────────────

    def _ensure_store(self) -> QdrantStore:
        if self._store is None:
            self._store = QdrantStore(
                QdrantConfig(
                    mode="embedded",
                    path=str(self.qdrant_path),
                    collection_name=self.kb_name,
                    dense_dimension=self.embedding_dim,
                    enable_sparse=self.enable_sparse,
                )
            )
        return self._store

    def _ensure_embedder(self) -> Embedder:
        if self._embedder is None:
            from .embedder import HuggingFaceEmbeddingModel

            model = HuggingFaceEmbeddingModel(
                model_name=self.embedding_model_name,
                device=self.device,
            )
            self._embedding_model = model
            self._embedder = Embedder(model=model, cache=None, batch_size=32)
        return self._embedder

    # ── 文件级解析（同 Pipeline._parse） ──────────────────────────────

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
            logger.warning("解析失败 %s: %s", file_path.name, e)
            return None

    # ── 核心入口：sync ───────────────────────────────────────────────

    def sync(self, source_folder_override: Optional[str] = None) -> dict:
        """一次性完成 scan → apply → BM25 dirty 检查。

        Args:
            source_folder_override: 可选，临时覆盖源文件夹路径

        Returns:
            报告 dict:
                {"added": int, "modified": int, "deleted": int,
                 "bm25_dirty": bool, "total_documents": int, "total_chunks": int}
        """
        folder = Path(source_folder_override) if source_folder_override else self.source_folder
        if not folder.exists():
            return {
                "error": f"源文件夹不存在: {folder}",
                "added": 0, "modified": 0, "deleted": 0,
            }

        # 1. 变更检测
        changes = scan(folder, self.manifest)

        if not changes.has_changes:
            return {
                "added": 0, "modified": 0, "deleted": 0,
                "bm25_dirty": self.manifest.bm25.dirty,
                "total_documents": self.manifest.total_documents,
                "total_chunks": self.manifest.total_chunks,
                "updated_at": self.manifest.updated_at,
            }

        # 2. 执行变更
        report = self.apply(changes, folder)

        # 3. BM25 dirty 状态更新
        self._update_bm25_dirty(changes)

        return report

    def scan(self) -> ChangeSet:
        """仅执行变更检测，不修改数据。

        Returns:
            ChangeSet
        """
        return scan(self.source_folder, self.manifest)

    # ── 执行变更 ────────────────────────────────────────────────────

    def apply(self, changes: ChangeSet, source_folder: Optional[Path] = None) -> dict:
        """执行变更集：新增/修改/删除。

        Args:
            changes: 变更集
            source_folder: 源文件夹（默认 self.source_folder）

        Returns:
            操作报告 dict
        """
        folder = source_folder or self.source_folder
        store = self._ensure_store()
        report = {
            "added": 0,
            "modified": 0,
            "deleted": 0,
            "total_documents": 0,
            "total_chunks": 0,
        }

        # ── 删除 ──
        for rel_path in changes.deleted:
            entry = self.manifest.files.get(rel_path)
            if entry:
                try:
                    store.delete_by_doc_id(entry.doc_id)
                except Exception as e:
                    logger.warning("删除索引失败 %s: %s", rel_path, e)
                del self.manifest.files[rel_path]
            report["deleted"] += 1
            logger.info("  [删除] %s", rel_path)

        # ── 新增 + 修改 ──
        all_new_chunks: list[Chunk] = []
        file_entries: list[tuple[str, FileEntry]] = []  # (rel_path, entry)

        for rel_path in list(changes.added) + list(changes.modified):
            full_path = folder / rel_path

            if not full_path.exists():
                logger.warning("文件不存在，跳过: %s", full_path)
                continue

            # 修改：先删除旧索引
            if rel_path in changes.modified:
                entry = self.manifest.files.get(rel_path)
                if entry:
                    try:
                        store.delete_by_doc_id(entry.doc_id)
                    except Exception as e:
                        logger.warning("删除旧索引失败 %s: %s", rel_path, e)
                report["modified"] += 1
                logger.info("  [修改] %s", rel_path)
            else:
                report["added"] += 1
                logger.info("  [新增] %s", rel_path)

            # 解析
            doc = self._parse(full_path)
            if doc is None:
                continue

            # 覆盖确定性 doc_id
            content_bytes = full_path.read_bytes()
            doc.doc_id = compute_doc_id(content_bytes)

            # 分块
            if self.enable_parent_child:
                pc_result = self._pc_chunker.build(doc)
                chunks = pc_result.parent_chunks + pc_result.child_chunks
            else:
                chunks = self._chunker.chunk(doc)

            all_new_chunks.extend(chunks)
            file_entries.append((
                rel_path,
                FileEntry(
                    doc_id=doc.doc_id,
                    content_hash=compute_content_hash(full_path),
                    size=len(content_bytes),
                    indexed_at=datetime.now().isoformat(),
                    chunk_count=len(chunks),
                ),
            ))

        # ── 嵌入 + 写入 Qdrant ──
        if all_new_chunks:
            embedder = self._ensure_embedder()
            records = embedder.embed_chunks(all_new_chunks)
            store.upsert(records)
            logger.info("  写入 %d 条 embedding 记录", len(records))

        # ── 更新 manifest ──
        for rel_path, entry in file_entries:
            self.manifest.files[rel_path] = entry

        # ── 汇总统计 ──
        self.manifest.total_documents = len(self.manifest.files)
        self.manifest.total_chunks = self._count_manifest_chunks()
        self.manifest.updated_at = datetime.now().isoformat()
        self.manifest.save(self.manifest_path)

        report["total_documents"] = self.manifest.total_documents
        report["total_chunks"] = self.manifest.total_chunks
        report["updated_at"] = self.manifest.updated_at
        report["bm25_dirty"] = self.manifest.bm25.dirty

        return report

    # ── BM25 dirty 追踪 ────────────────────────────────────────────

    def _update_bm25_dirty(self, changes: ChangeSet) -> None:
        """根据变更比例更新 BM25 dirty 状态。"""
        total = len(self.manifest.files)
        if total == 0:
            return
        changed = len(changes.modified) + len(changes.deleted)
        dirty_ratio = changed / total
        if dirty_ratio > 0.1:
            self.manifest.bm25.dirty = True
            self.manifest.bm25.dirty_ratio = dirty_ratio
            logger.info(
                "BM25 标记 dirty (dirty_ratio=%.2f > 0.1)", dirty_ratio
            )
        # dirty_ratio <= 0.1 → 不标记（但已 dirty 则保持）

    def _count_manifest_chunks(self) -> int:
        """根据 manifest 中所有 FileEntry 的 chunk_count 求和。"""
        return sum(e.chunk_count for e in self.manifest.files.values())

    # ── BM25 重算 ────────────────────────────────────────────────────

    def rebuild_bm25(self) -> dict:
        """全量重算 BM25 sparse vectors。

        流程：
        1. scroll 所有 chunk 文本
        2. 重新 fit BM25Encoder
        3. 批量更新所有点的 sparse vectors
        4. 清除 dirty 标记

        Returns:
            {"status": str, "chunks_processed": int}
        """
        store = self._ensure_store()

        # 1. 获取全部文本
        texts_data = store.scroll_texts()
        if not texts_data:
            logger.info("无数据，跳过 BM25 重算")
            return {"status": "skipped", "chunks_processed": 0}

        all_texts = [t[2] for t in texts_data]  # (point_id, chunk_id, text)
        logger.info("BM25 重算: %d 个 chunk", len(all_texts))

        # 2. 重新拟合 BM25Encoder
        encoder = BM25Encoder()
        encoder.fit(all_texts)

        # 3. 批量更新 sparse vectors
        from qdrant_client import models as _models

        updated = 0
        batch = []
        for point_id, chunk_id, text in texts_data:
            sparse_vec = encoder.encode_document(text)
            batch.append(
                _models.PointStruct(
                    id=point_id,
                    vector={"sparse": sparse_vec},
                    payload={},  # 只更新向量，payload 不变
                )
            )
            if len(batch) >= 100:
                store.client.upsert(
                    collection_name=store.collection_name,
                    points=batch,
                )
                updated += len(batch)
                batch = []
        if batch:
            store.client.upsert(
                collection_name=store.collection_name,
                points=batch,
            )
            updated += len(batch)

        # 4. 将新 encoder 设置回 store（供当前进程使用）
        store.set_bm25_encoder(encoder)

        # 5. 持久化 encoder 到磁盘（供后续加载沿用）
        qdrant_path = Path(self.qdrant_path)
        qdrant_path.mkdir(parents=True, exist_ok=True)
        encoder.save(str(qdrant_path / "bm25_encoder.pkl"))

        # 6. 清除 dirty 标记
        self.manifest.bm25.dirty = False
        self.manifest.bm25.dirty_ratio = 0.0
        self.manifest.bm25.last_rebuild_at = datetime.now().isoformat()
        self.manifest.updated_at = datetime.now().isoformat()
        self.manifest.save(self.manifest_path)

        logger.info("BM25 重算完成: %d 个向量已更新", updated)
        return {"status": "success", "chunks_processed": updated}

    # ── 全量重建 ────────────────────────────────────────────────────

    def rebuild_full(self) -> dict:
        """全量重建索引：清空 → 重新索引所有文件。

        适用于：
        - 首次从旧 ID 体系迁移到确定性 ID
        - Manual 全量刷新

        Returns:
            操作报告 dict
        """
        store = self._ensure_store()

        # 1. 清空现有的所有 points
        try:
            from qdrant_client import models as _models

            store.client.delete(
                collection_name=store.collection_name,
                points_selector=_models.FilterSelector(
                    filter=_models.Filter(
                        must=[_models.FieldCondition(
                            key="doc_id",
                            match=_models.MatchValue(value="__dummy__"),
                        )]
                    )
                ),
            )
            # 更好的办法：直接删 collection
            try:
                store.client.delete_collection(store.collection_name)
            except Exception:
                pass
            store._collection_ready = False
            store._indexes_created = False
            store._bm25_encoder = None
        except Exception as e:
            logger.warning("清空 collection 失败: %s", e)

        # 2. 重置 manifest
        self.manifest = Manifest.create(
            kb_name=self.kb_name,
            source_folder=str(self.source_folder),
        )

        # 3. 扫描所有文件当作"新增"处理
        changes = ChangeSet(
            added=[str(p.relative_to(self.source_folder))
                   for p in self.source_folder.rglob("*")
                   if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()]
        )

        if not changes.has_changes:
            self.manifest.updated_at = datetime.now().isoformat()
            self.manifest.save(self.manifest_path)
            return {
                "added": 0, "modified": 0, "deleted": 0,
                "total_documents": 0, "total_chunks": 0,
            }

        report = self.apply(changes)

        # 4. 重建 BM25
        bm25_report = self.rebuild_bm25()
        report["bm25_rebuild"] = bm25_report

        return report

    # ── 状态查询 ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """返回索引状态概览。"""
        store = self._ensure_store()
        try:
            store_count = store.count()
        except Exception:
            store_count = 0

        return {
            "kb_name": self.kb_name,
            "source_folder": self.source_folder,
            "total_documents": self.manifest.total_documents,
            "total_chunks": self.manifest.total_chunks,
            "qdrant_points": store_count,
            "bm25_dirty": self.manifest.bm25.dirty,
            "bm25_dirty_ratio": self.manifest.bm25.dirty_ratio,
            "bm25_last_rebuild_at": self.manifest.bm25.last_rebuild_at,
            "updated_at": self.manifest.updated_at,
            "manifest_path": str(self.manifest_path),
        }


# ── 搜索路径触发函数 ──────────────────────────────────────────


def check_and_rebuild_bm25(
    store: QdrantStore,
    kb_name: str,
    config,
) -> None:
    """检查 BM25 dirty 状态并在需要时触发重算。

    在知识库加载时（OnlineEngine.load_index）调用此函数，
    实现「搜索路径中触发 BM25 延迟重算」的需求。

    Args:
        store: 已连接的 QdrantStore
        kb_name: 知识库名称
        config: ConfigManager 实例
    """
    index_store = Path(config.get("index_store_dir", "data/indices"))
    manifest_path = index_store / kb_name / "manifest.json"

    if not manifest_path.exists():
        return

    try:
        manifest = Manifest.load(manifest_path)
    except Exception:
        return

    if not manifest.bm25.dirty or manifest.bm25.dirty_ratio <= 0.1:
        return

    logger.info(
        "BM25 延迟重算触发 (dirty_ratio=%.2f > 0.1)", manifest.bm25.dirty_ratio
    )

    from qdrant_client import models as _models

    # 1. 获取全部文本
    texts_data = store.scroll_texts()
    if not texts_data:
        return

    all_texts = [t[2] for t in texts_data]
    logger.info("BM25 重算: %d 个 chunk", len(all_texts))

    # 2. 重新拟合
    encoder = BM25Encoder()
    encoder.fit(all_texts)

    # 3. 批量更新 sparse vectors
    updated = 0
    batch = []
    for point_id, chunk_id, text in texts_data:
        sparse_vec = encoder.encode_document(text)
        batch.append(
            _models.PointStruct(
                id=point_id,
                vector={"sparse": sparse_vec},
                payload={},
            )
        )
        if len(batch) >= 100:
            store.client.upsert(collection_name=store.collection_name, points=batch)
            updated += len(batch)
            batch = []
    if batch:
        store.client.upsert(collection_name=store.collection_name, points=batch)
        updated += len(batch)

    # 4. 更新 store 的 encoder
    store.set_bm25_encoder(encoder)

    # 5. 持久化 encoder 到磁盘
    qdrant_path = Path(store.config.path)
    qdrant_path.mkdir(parents=True, exist_ok=True)
    encoder.save(str(qdrant_path / "bm25_encoder.pkl"))

    # 6. 清除 manifest dirty 标记
    manifest.bm25.dirty = False
    manifest.bm25.dirty_ratio = 0.0
    manifest.bm25.last_rebuild_at = datetime.now().isoformat()
    manifest.updated_at = datetime.now().isoformat()
    manifest.save(manifest_path)

    logger.info("BM25 延迟重算完成: %d 个向量已更新", updated)
