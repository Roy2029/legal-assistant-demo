from pydantic import BaseModel
from typing import List, Optional
from dataclasses import dataclass
from .modules import BaseStore
from .data_model import Chunk, EmbeddingRecord, RetrievalResult, SearchQuery
import pickle
from pathlib import Path
import warnings
import re
import numpy as np

# ── Qdrant 依赖（延迟导入，允许在没有 qdrant-client 时仍可使用其他 store） ──
_qdrant_client = None
_SparseVector = None
_models = None


def _ensure_qdrant():
    """延迟导入 Qdrant 依赖，首次使用时加载。"""
    global _qdrant_client, _SparseVector, _models
    if _qdrant_client is None:
        try:
            from qdrant_client import QdrantClient as _QdrantClient
            from qdrant_client.models import SparseVector as _SparseVector
            from qdrant_client import models as _models

            _qdrant_client = _QdrantClient
            _SparseVector = _SparseVector
            _models = _models
        except ImportError:
            raise ImportError(
                "qdrant-client 未安装。请运行: pip install qdrant-client>=1.7.0"
            )


# ── 旧依赖（延迟导入，标记 deprecated） ──
def _ensure_faiss():
    try:
        import faiss  # noqa: F401
    except ImportError:
        raise ImportError(
            "faiss-cpu 未安装。FAISS 已废弃，推荐迁移到 Qdrant。"
            "如需继续使用 FAISS: pip install faiss-cpu>=1.7.4"
        )


def _ensure_bm25_okapi():
    try:
        from rank_bm25 import BM25Okapi  # noqa: F401
    except ImportError:
        raise ImportError(
            "rank-bm25 未安装。BM25Okapi 已废弃，推荐迁移到 Qdrant SparseVector。"
        )


def _normalize(vector):
    vector = np.array(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm == 0:
        return vector
    return vector / norm


# ══════════════════════════════════════════════════════════════════════════════
# 新版 Qdrant 存储体系
# ══════════════════════════════════════════════════════════════════════════════

class BM25Encoder:
    """基于 jieba 分词的 BM25 稀疏向量编码器。

    索引阶段：在 chunk 集合上拟合 → compute IDF → encode texts as SparseVector。
    查询阶段：encode query text as SparseVector。

    持久化：通过 pickle 保存/加载词汇表和 IDF 值。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._term_to_id: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0
        self._doc_count: int = 0
        self._doc_lengths: list[int] = []
        self._fitted = False

    # ── tokenizer ────────────────────────────────────────────────

    def tokenize(self, text: str) -> list[str]:
        """使用 jieba 分词（中文）并过滤掉空白 token。"""
        import jieba
        tokens = jieba.lcut(text)
        return [t.strip() for t in tokens if t.strip()]

    # ── fit ──────────────────────────────────────────────────────

    def fit(self, texts: list[str]) -> "BM25Encoder":
        """在语料库上拟合编码器：统计 DF、计算 IDF。"""
        if not texts:
            return self

        doc_tokens: list[list[str]] = []
        df: dict[str, int] = {}
        doc_lengths: list[int] = []

        for text in texts:
            tokens = self.tokenize(text)
            doc_tokens.append(tokens)
            doc_lengths.append(len(tokens))
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        N = len(texts)
        self._doc_count = N
        self._doc_lengths = doc_lengths
        self._avgdl = sum(doc_lengths) / N if N > 0 else 0.0

        # 建立 term -> id 映射
        self._term_to_id = {term: i for i, term in enumerate(sorted(df.keys()))}

        # 计算 IDF: IDF(t) = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)
        for term, df_t in df.items():
            self._idf[term] = np.log((N - df_t + 0.5) / (df_t + 0.5) + 1.0)

        self._fitted = True
        return self

    def partial_fit(self, new_texts: list[str]) -> "BM25Encoder":
        """增量拟合：在已有统计基础上更新（简化实现：全量重新拟合）。

        注意：当前实现为全量重新拟合。如果 texts 中包含已在原始语料中的文本，
        调用者需要传入完整的 texts 列表。
        """
        # 简化：合并现有语料 + 新文本重新拟合
        # 实际使用中，建议调用者收集全部 chunks 后一次性 fit()
        if not self._fitted:
            return self.fit(new_texts)
        return self.fit(new_texts)

    # ── tokenize with weights（诊断/可视化） ──────────────────────

    def tokenize_with_weights(self, text: str) -> list[dict]:
        """分词并返回每个 token 的诊断信息。

        Returns:
            list[dict]: 每项包含:
                - token (str): 分词结果
                - idf (float): IDF 值（OOV 为 0.0）
                - is_oov (bool): 是否词表外词
                - bm25_score (float): 该 token 对查询的 BM25 贡献分
                - freq (int): 在查询中的出现次数
        """
        tokens = self.tokenize(text)
        if not tokens:
            return []

        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        result = []
        for token in tokens:
            term_id = self._term_to_id.get(token)
            is_oov = term_id is None
            idf = self._idf.get(token, 0.0) if not is_oov else 0.0
            freq = tf.get(token, 1)
            # BM25 query weight: IDF * ((k1 + 1) * qtf) / (k1 + qtf)
            bm25_score = idf * ((self.k1 + 1.0) * freq) / (self.k1 + freq) if not is_oov else 0.0
            result.append({
                "token": token,
                "idf": round(idf, 4),
                "is_oov": is_oov,
                "bm25_score": round(bm25_score, 4),
                "freq": freq,
            })

        return result

    # ── encode document（索引阶段） ───────────────────────────────

    def encode_document(self, text: str) -> "SparseVector":
        """将文档/chunk 文本编码为 BM25 稀疏向量。"""
        _ensure_qdrant()

        tokens = self.tokenize(text)
        doc_len = len(tokens)
        if doc_len == 0:
            return _SparseVector(indices=[], values=[])

        # 计算 term frequencies
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        indices: list[int] = []
        values: list[float] = []

        for term, freq in tf.items():
            term_id = self._term_to_id.get(term)
            if term_id is None:
                continue  # OOV term
            idf = self._idf.get(term, 0.0)
            # BM25 term weight for document
            numerator = freq * (self.k1 + 1.0)
            denominator = freq + self.k1 * (1.0 - self.b + self.b * doc_len / self._avgdl)
            weight = idf * numerator / denominator
            if weight > 0:
                indices.append(term_id)
                values.append(float(weight))

        return _SparseVector(indices=indices, values=values)

    # ── encode query（检索阶段） ──────────────────────────────────

    def encode_query(self, text: str) -> "SparseVector":
        """将查询文本编码为 BM25 稀疏向量（仅 IDF 加权）。"""
        _ensure_qdrant()

        tokens = self.tokenize(text)
        if not tokens:
            return _SparseVector(indices=[], values=[])

        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        indices: list[int] = []
        values: list[float] = []

        for term, freq in tf.items():
            term_id = self._term_to_id.get(term)
            if term_id is None:
                continue
            idf = self._idf.get(term, 0.0)
            # Query weight: IDF * ((k1 + 1) * qtf) / (k1 + qtf)
            weight = idf * ((self.k1 + 1.0) * freq) / (self.k1 + freq)
            if weight > 0:
                indices.append(term_id)
                values.append(float(weight))

        return _SparseVector(indices=indices, values=values)

    # ── 持久化 ───────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """保存编码器状态到文件（pickle）。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "k1": self.k1,
            "b": self.b,
            "term_to_id": self._term_to_id,
            "idf": self._idf,
            "avgdl": self._avgdl,
            "doc_count": self._doc_count,
            "doc_lengths": self._doc_lengths,
            "fitted": self._fitted,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)

    @classmethod
    def load(cls, path: str) -> "BM25Encoder":
        """从文件加载编码器状态。"""
        with open(path, "rb") as f:
            state = pickle.load(f)
        encoder = cls(k1=state["k1"], b=state["b"])
        encoder._term_to_id = state["term_to_id"]
        encoder._idf = state["idf"]
        encoder._avgdl = state["avgdl"]
        encoder._doc_count = state["doc_count"]
        encoder._doc_lengths = state["doc_lengths"]
        encoder._fitted = state["fitted"]
        return encoder

    @property
    def vocab_size(self) -> int:
        return len(self._term_to_id)


@dataclass
class QdrantConfig:
    """Qdrant 存储配置。

    支持嵌入式（path）和服务式（host+port）两种部署模式，
    以及 dense/sparse 向量和索引参数配置。
    """

    # 部署模式
    mode: str = "embedded"  # "embedded" | "server"
    path: str = "./qdrant_data"
    host: str = "localhost"
    port: int = 6333
    api_key: Optional[str] = None
    prefer_grpc: bool = False

    # Collection
    collection_name: str = "default"

    # Dense 向量
    dense_dimension: int = 384
    dense_distance: str = "Cosine"
    dense_on_disk: bool = True
    dense_quantization: Optional[str] = None  # "scalar" | "product" | "binary" | None
    dense_indexing_threshold: int = 20_000

    # Sparse 向量（BM25）
    enable_sparse: bool = True
    bm25_tokenizer: str = "jieba"  # 预留扩展

    # 默认检索参数
    default_mode: str = "hybrid"
    default_fusion: str = "rrf"

    @classmethod
    def from_config_dict(cls, d: dict) -> "QdrantConfig":
        """从配置字典创建 QdrantConfig（兼容 config/default.yaml 结构）。"""
        dense = d.get("dense", {})
        sparse = d.get("sparse", {})
        return cls(
            mode=d.get("mode", "embedded"),
            path=d.get("path", "./qdrant_data"),
            host=d.get("host", "localhost"),
            port=d.get("port", 6333),
            api_key=d.get("api_key"),
            prefer_grpc=d.get("prefer_grpc", False),
            collection_name=d.get("collection_name", "default"),
            dense_dimension=dense.get("dimension", 384),
            dense_distance=dense.get("distance", "Cosine"),
            dense_on_disk=dense.get("on_disk", True),
            dense_quantization=dense.get("quantization"),
            dense_indexing_threshold=dense.get("indexing_threshold", 20_000),
            enable_sparse=sparse.get("enabled", True),
            bm25_tokenizer=sparse.get("tokenizer", "jieba"),
            default_mode=d.get("search", {}).get("mode", "hybrid"),
            default_fusion=d.get("search", {}).get("fusion", "rrf"),
        )


class QdrantStore(BaseStore[SearchQuery, EmbeddingRecord]):
    """统一向量/关键词存储，封装 Qdrant 客户端。

    一个 Collection 同时持有 dense 向量和 sparse 向量（可选），
    通过 SearchQuery.mode 切换 dense / sparse / hybrid 检索模式。

    持久化：嵌入式模式自动写入本地磁盘，服务式模式数据在远端。
    """

    def __init__(self, config: QdrantConfig):
        _ensure_qdrant()

        self.config = config
        if config.mode == "embedded":
            self.client = _qdrant_client(path=config.path)
        else:
            self.client = _qdrant_client(
                host=config.host,
                port=config.port,
                api_key=config.api_key,
                prefer_grpc=config.prefer_grpc,
            )

        self.collection_name = config.collection_name
        self._bm25_encoder: Optional[BM25Encoder] = None
        self._collection_ready = False
        self._indexes_created = False

    def close(self) -> None:
        """关闭 Qdrant 客户端连接，释放文件锁（嵌入式模式）。"""
        if hasattr(self, 'client') and self.client is not None:
            self.client.close()
            self.client = None

    def __del__(self) -> None:
        """析构时自动关闭连接。"""
        try:
            self.close()
        except Exception:
            pass

    # ── Collection 管理 ───────────────────────────────────────────

    def _ensure_collection(self, records: list[EmbeddingRecord]) -> None:
        """确保 Collection 存在，不存在则创建。"""
        if self._collection_ready:
            return

        try:
            self.client.get_collection(self.collection_name)
            self._collection_ready = True
            return
        except Exception:
            pass

        # 构建向量配置
        vectors_config = {
            "dense": _models.VectorParams(
                size=self.config.dense_dimension,
                distance=self.config.dense_distance,
                on_disk=self.config.dense_on_disk,
            ),
        }

        sparse_vectors_config = None
        if self.config.enable_sparse:
            sparse_vectors_config = {
                "sparse": _models.SparseVectorParams(),
            }

        # 量化配置
        quantization_config = None
        if self.config.dense_quantization:
            q_map = {
                "scalar": _models.ScalarQuantization,
                "product": _models.ProductQuantization,
                "binary": _models.BinaryQuantization,
            }
            q_cls = q_map.get(self.config.dense_quantization)
            if q_cls:
                quantization_config = q_cls(
                    scalar=_models.ScalarQuantizationConfig(
                        type=_models.ScalarType.INT8,
                        quantile=0.99,
                        always_ram=True,
                    ),
                )

        # HNSW 索引
        hnsw_config = None
        num_records = len(records)
        if num_records >= self.config.dense_indexing_threshold:
            hnsw_config = _models.HnswConfigDiff(
                m=16,
                ef_construct=100,
            )

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
            hnsw_config=hnsw_config,
            quantization_config=quantization_config,
        )
        self._collection_ready = True

    # ── Upsert ────────────────────────────────────────────────────

    def _upsert_impl(self, records: list[EmbeddingRecord]) -> None:
        if not records:
            return

        _ensure_qdrant()
        self._ensure_collection(records)

        # 如果需要 sparse 且 BM25Encoder 未拟合：自动拟合
        if self.config.enable_sparse and self._bm25_encoder is None:
            self._bm25_encoder = BM25Encoder()
            texts = [r.chunk.text for r in records]
            self._bm25_encoder.fit(texts)

        points = []
        for i, record in enumerate(records):
            vector_data = {"dense": list(record.vector)}

            payload = {
                "chunk_id": record.chunk.chunk_id,
                "doc_id": record.chunk.doc_id,
                "text": record.chunk.text,
                "metadata": record.chunk.metadata,
                "heading_path": record.chunk.heading_path,
                "block_ids": record.chunk.block_ids,
                "order": record.chunk.order,
                "token_count": record.chunk.token_count,
                "chunk_level": record.chunk.chunk_level,
                "parent_chunk_id": record.chunk.parent_chunk_id,
                "child_chunk_ids": record.chunk.child_chunk_ids,
                "embedding_model": record.embedding_model,
                "content_hash": record.content_hash,
            }

            point_id = abs(hash(record.chunk.chunk_id)) % (2**63)

            if self.config.enable_sparse and self._bm25_encoder is not None:
                sparse_vec = self._bm25_encoder.encode_document(record.chunk.text)
                vector_data["sparse"] = sparse_vec

            point = _models.PointStruct(
                id=point_id,
                vector=vector_data,
                payload=payload,
            )
            points.append(point)

        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        # 首次 upsert 完成后建立 payload indexes（仅一次）
        if not self._indexes_created:
            self.create_payload_indexes()
            self._indexes_created = True

    # ── Search ────────────────────────────────────────────────────

    def _search_impl(self, query: SearchQuery, top_k: int = 5) -> list[RetrievalResult]:
        _ensure_qdrant()

        # 将 SearchQuery.filters 转为 Qdrant Filter 对象
        qdrant_filter = None
        if query.filters:
            qdrant_filter = _models.Filter(**query.filters)

        if query.mode == "dense":
            if query.dense_vector is None:
                raise ValueError("dense 模式需要 dense_vector")
            results = self.client.query_points(
                collection_name=self.collection_name,
                using="dense",
                query=list(query.dense_vector),
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            ).points

        elif query.mode == "sparse":
            if not self.config.enable_sparse or self._bm25_encoder is None:
                raise ValueError("sparse 模式需要 enable_sparse=True 且已索引 BM25 向量")
            sparse_vec = self._bm25_encoder.encode_query(query.text)
            results = self.client.query_points(
                collection_name=self.collection_name,
                using="sparse",
                query=sparse_vec,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            ).points

        elif query.mode == "hybrid":
            if query.dense_vector is None:
                raise ValueError("hybrid 模式需要 dense_vector")
            if not self.config.enable_sparse or self._bm25_encoder is None:
                raise ValueError("hybrid 模式需要 enable_sparse=True 且已索引 BM25 向量")

            sparse_vec = self._bm25_encoder.encode_query(query.text)

            results = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    _models.Prefetch(
                        using="dense",
                        query=list(query.dense_vector),
                        limit=top_k,
                    ),
                    _models.Prefetch(
                        using="sparse",
                        query=sparse_vec,
                        limit=top_k,
                    ),
                ],
                query=_models.FusionQuery(fusion=_models.Fusion.RRF),
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            ).points

        else:
            raise ValueError(f"不支持的检索模式: {query.mode}")

        # 去重：Qdrant embedded 模式下 sparse/hybrid 可能返回相同 chunk 多次
        # 按 chunk_id 去重，保留首次出现的 score
        seen_chunk_ids: set[str] = set()
        deduped = []
        for r in results:
            cid = (r.payload or {}).get("chunk_id")
            if cid is None:
                # 无 payload 时按 point ID 兜底去重
                pid = str(r.id)
                if pid not in seen_chunk_ids:
                    seen_chunk_ids.add(pid)
                    deduped.append(r)
            elif cid not in seen_chunk_ids:
                seen_chunk_ids.add(cid)
                deduped.append(r)

        return [self._point_to_result(r) for r in deduped]

    def _point_to_result(self, scored_point) -> RetrievalResult:
        """将 Qdrant ScoredPoint 转为 RetrievalResult。"""
        payload = scored_point.payload or {}
        chunk = Chunk(
            chunk_id=payload.get("chunk_id", ""),
            doc_id=payload.get("doc_id", ""),
            text=payload.get("text", ""),
            metadata=payload.get("metadata", {}),
            block_ids=payload.get("block_ids", []),
            heading_path=payload.get("heading_path", []),
            order=payload.get("order", 0),
            token_count=payload.get("token_count"),
            chunk_level=payload.get("chunk_level", "single"),
            parent_chunk_id=payload.get("parent_chunk_id"),
            child_chunk_ids=payload.get("child_chunk_ids", []),
        )
        return RetrievalResult(
            score=float(scored_point.score),
            chunk=chunk,
            retrieval_type=payload.get("retrieval_type", "qdrant"),
        )

    # ── Payload Indexes ───────────────────────────────────────────

    def create_payload_indexes(self) -> None:
        """对常用过滤字段建立 Qdrant payload index。

        应在 Collection 创建后调用一次（在 _ensure_collection 的 upsert 完成后）。
        如果索引已存在则静默跳过。
        """
        filterable_fields = [
            ("doc_id", "keyword"),
            ("chunk_level", "keyword"),
            ("metadata.doc_type", "keyword"),
            ("metadata.department", "keyword"),
            ("metadata.valid_status", "keyword"),
        ]
        for field_name, field_schema in filterable_fields:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
            except Exception:
                pass  # 索引可能已存在

    # ── Batch retrieval by chunk_id ───────────────────────────────

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, Chunk]:
        """批量按 chunk_id 检索 Chunk 对象。

        用于 Parent-Child 策略的第二阶段：根据 child 返回的 parent_chunk_id
        回查完整的 parent Chunk。

        使用 Qdrant client.retrieve() 通过内部 point ID 定位。
        """
        _ensure_qdrant()
        if not chunk_ids:
            return {}

        point_ids = [abs(hash(cid)) % (2 ** 63) for cid in chunk_ids]
        records = self.client.retrieve(
            collection_name=self.collection_name,
            ids=point_ids,
            with_payload=True,
            with_vectors=False,
        )
        result: dict[str, Chunk] = {}
        for r in records:
            chunk = self._point_to_chunk(r)
            if chunk and chunk.chunk_id in chunk_ids:
                result[chunk.chunk_id] = chunk
        return result

    # ── Summary upsert ────────────────────────────────────────────

    def upsert_summary(self, record: EmbeddingRecord) -> None:
        """写入摘要点。自动强制 chunk_level="document"。

        与常规 chunk 写入同一 Collection（通过 chunk_level 区分层级）。
        """
        record.chunk.chunk_level = "document"
        self.upsert([record])

    # ── 扩展接口 ──────────────────────────────────────────────────

    def scroll(self, filters: Optional[dict] = None, batch_size: int = 100) -> list[Chunk]:
        """遍历所有 chunk（供 chunk_export 等工具使用）。"""
        _ensure_qdrant()

        all_chunks: list[Chunk] = []
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not records:
                break

            for record in records:
                chunk = self._point_to_chunk(record)
                if chunk:
                    all_chunks.append(chunk)

            if next_offset is None:
                break
            offset = next_offset

        return all_chunks

    def scroll_paginated(
        self,
        filter_condition: Optional[dict] = None,
        limit: int = 50,
        offset: Optional[int] = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> tuple[list[Chunk], Optional[int]]:
        """分页遍历 chunk。

        Args:
            filter_condition: Qdrant Filter dict（可选）
            limit: 每页条数
            offset: 起始偏移（Qdrant 内部偏移 ID，首次传 None）
            with_payload: 是否返回 payload
            with_vectors: 是否返回向量

        Returns:
            (chunks, next_offset) — next_offset 为 None 表示无更多数据
        """
        _ensure_qdrant()
        qdrant_filter = _models.Filter(**filter_condition) if filter_condition else None
        records, next_offset = self.client.scroll(
            collection_name=self.collection_name,
            limit=limit,
            offset=offset,
            with_payload=with_payload,
            with_vectors=with_vectors,
            scroll_filter=qdrant_filter,
        )
        chunks = []
        for record in records:
            chunk = self._point_to_chunk(record)
            if chunk:
                chunks.append(chunk)
        return chunks, next_offset

    def _point_to_chunk(self, record) -> Optional[Chunk]:
        """从 Qdrant Record 提取 Chunk（不含 score）。"""
        payload = record.payload
        if payload is None:
            return None
        return Chunk(
            chunk_id=payload.get("chunk_id", ""),
            doc_id=payload.get("doc_id", ""),
            text=payload.get("text", ""),
            metadata=payload.get("metadata", {}),
            block_ids=payload.get("block_ids", []),
            heading_path=payload.get("heading_path", []),
            order=payload.get("order", 0),
            token_count=payload.get("token_count"),
            chunk_level=payload.get("chunk_level", "single"),
            parent_chunk_id=payload.get("parent_chunk_id"),
            child_chunk_ids=payload.get("child_chunk_ids", []),
        )

    def delete_by_doc_id(self, doc_id: str) -> None:
        """按文档 ID 删除所有关联 chunk（增量更新预留）。"""
        _ensure_qdrant()
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=_models.FilterSelector(
                filter=_models.Filter(
                    must=[
                        _models.FieldCondition(
                            key="doc_id",
                            match=_models.MatchValue(value=doc_id),
                        )
                    ]
                )
            ),
        )

    def count(self, filters: Optional[dict] = None) -> int:
        """统计 Collection 中的 point 数量。"""
        try:
            info = self.client.get_collection(self.collection_name)
            return info.points_count
        except Exception:
            return 0

    def scroll_texts(self, batch_size: int = 100) -> list[tuple[int, str, str]]:
        """滚动获取所有 chunk 的 point_id、chunk_id 和 text。

        用于 BM25 全量重算。返回 (internal_point_id, chunk_id, text) 三元组列表。
        """
        _ensure_qdrant()
        results: list[tuple[int, str, str]] = []
        offset = None
        while True:
            points, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for p in points:
                chunk_id = (p.payload or {}).get("chunk_id", "")
                text = (p.payload or {}).get("text", "")
                results.append((p.id, chunk_id, text))
            if next_offset is None:
                break
            offset = next_offset
        return results

    def set_bm25_encoder(self, encoder: BM25Encoder) -> None:
        """手动设置已拟合的 BM25Encoder（用于从文件加载后重用）。"""
        self._bm25_encoder = encoder

    @property
    def bm25_encoder(self) -> Optional[BM25Encoder]:
        return self._bm25_encoder

    # ── 持久化 ───────────────────────────────────────────────────

    def _save_impl(self, path: str) -> None:
        """嵌入式模式：Qdrant 自动持久化，此处仅保存 BM25 encoder 状态。

        服务式模式：数据在远端，本地仅保存 BM25 encoder。
        """
        if self.config.mode == "embedded":
            # Qdrant 嵌入式已自动写入磁盘，无需操作
            pass

        # 持久化 BM25 encoder
        if self._bm25_encoder is not None:
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            self._bm25_encoder.save(str(p / "bm25_encoder.pkl"))

    def _load_impl(self, path: str) -> None:
        """加载：重连 Collection 并恢复 BM25 encoder。"""
        p = Path(path)
        bm25_encoder_path = p / "bm25_encoder.pkl"
        if bm25_encoder_path.exists() and self.config.enable_sparse:
            self._bm25_encoder = BM25Encoder.load(str(bm25_encoder_path))

        # 标记 collection 待验证（首次 ops 时会自动检查/创建）
        self._collection_ready = False
        # 加载时 payload indexes 应已存在，跳过重建
        self._indexes_created = True


# ══════════════════════════════════════════════════════════════════════════════
# 旧版存储实现（保留兼容，标记 deprecated）
# ══════════════════════════════════════════════════════════════════════════════

class InMemoryStore(BaseStore[List[float], EmbeddingRecord]):
    """内存存储，适合测试和小规模场景。不受 Qdrant 迁移影响，继续使用。"""

    def __init__(self):
        self.data: list[EmbeddingRecord] = []

    def _upsert_impl(self, records: list[EmbeddingRecord]) -> None:
        self.data.extend(records)

    def _search_impl(self, query_vector: List[float], top_k: int = 5) -> list[RetrievalResult]:
        query = np.array(query_vector, dtype=np.float32)
        scored = []
        for record in self.data:
            vec = np.array(record.vector, dtype=np.float32)
            score = float(np.dot(query, vec))
            scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievalResult(
                score=score,
                chunk=record.chunk,
                retrieval_type="in_memory",
            )
            for score, record in scored[:top_k]
        ]

    def _save_impl(self, path: str) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "records.pkl", "wb") as f:
            pickle.dump(self.data, f)

    def _load_impl(self, path: str) -> None:
        path = Path(path)
        with open(path / "records.pkl", "rb") as f:
            self.data = pickle.load(f)


class FAISSStore(BaseStore[List[float], EmbeddingRecord]):
    """⚠️ 已废弃：请使用 QdrantStore。

    保留此实现用于兼容旧索引数据加载。
    新项目请使用 QdrantStore。
    """

    def __init__(self, dimension: int):
        _ensure_faiss()
        import faiss
        warnings.warn(
            "FAISSStore 已废弃，请迁移到 QdrantStore。"
            "详情见 openspec/changes/migrate-faiss-to-qdrant/",
            DeprecationWarning,
            stacklevel=2,
        )
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        self.records: dict[int, EmbeddingRecord] = {}
        self.next_id = 0

    def _upsert_impl(self, records: list[EmbeddingRecord]) -> None:
        vectors = []
        ids = []
        for record in records:
            vector = _normalize(record.vector)
            vectors.append(vector)
            internal_id = self.next_id
            self.records[internal_id] = record
            ids.append(internal_id)
            self.next_id += 1
        vectors = np.array(vectors, dtype=np.float32)
        self.index.add(vectors)

    def _search_impl(self, query_vector: List[float], top_k: int = 5) -> list[RetrievalResult]:
        query_vector = _normalize(query_vector)
        query_vector = np.array([query_vector], dtype=np.float32)
        scores, indices = self.index.search(query_vector, top_k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            record = self.records[idx]
            results.append(RetrievalResult(
                score=float(score),
                chunk=record.chunk,
                retrieval_type="faiss",
            ))
        return results

    def _save_impl(self, path: str) -> None:
        import faiss
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(path / "faiss.index"))
        with open(path / "records.pkl", "wb") as f:
            pickle.dump({"records": self.records, "next_id": self.next_id}, f)

    def _load_impl(self, path: str) -> None:
        import faiss
        path = Path(path)
        self.index = faiss.read_index(str(path / "faiss.index"))
        with open(path / "records.pkl", "rb") as f:
            data = pickle.load(f)
        self.records = data["records"]
        self.next_id = data["next_id"]


class SimpleTokenizer:
    """code-aware tokenizer
    camelCase split, snake_case split, multilingual.
    """

    def tokenize(self, text: str) -> list[str]:
        text = text.lower()
        return re.findall(r"\b\w+\b", text)


class BM25Document(BaseModel):
    """BM25Store 内部使用的文档表示（chunk + 预分词后的 tokens）。"""
    chunk_id: str
    tokens: list[str]
    chunk: Chunk


class BM25Store(BaseStore[str, Chunk]):
    """⚠️ 已废弃：请使用 QdrantStore（sparse 模式）。

    保留此实现用于兼容旧索引数据加载。
    新项目请使用 QdrantStore + BM25Encoder。
    """

    def __init__(self, tokenizer=None):
        _ensure_bm25_okapi()
        from rank_bm25 import BM25Okapi
        warnings.warn(
            "BM25Store 已废弃，请迁移到 QdrantStore (sparse 模式)。"
            "详情见 openspec/changes/migrate-faiss-to-qdrant/",
            DeprecationWarning,
            stacklevel=2,
        )
        self.tokenizer = tokenizer or SimpleTokenizer()
        self.documents: list[BM25Document] = []
        self.chunks: dict[str, Chunk] = {}
        self.bm25: "BM25Okapi | None" = None

    def _upsert_impl(self, records: list[Chunk]) -> None:
        from rank_bm25 import BM25Okapi
        corpus = []
        for chunk in records:
            tokens = self.tokenizer.tokenize(chunk.text)
            doc = BM25Document(
                chunk_id=chunk.chunk_id,
                tokens=tokens,
                chunk=chunk,
            )
            self.documents.append(doc)
            self.chunks[chunk.chunk_id] = chunk
            corpus.append(tokens)
        # BM25Okapi 不支持增量索引，需要重建
        all_corpus = [d.tokens for d in self.documents]
        self.bm25 = BM25Okapi(all_corpus)

    def _search_impl(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        query_tokens = self.tokenizer.tokenize(query)
        scores = self.bm25.get_scores(query_tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            doc = self.documents[idx]
            results.append(RetrievalResult(
                chunk=doc.chunk,
                score=float(scores[idx]),
                retrieval_type="bm25",
            ))
        return results

    def _save_impl(self, path: str) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "bm25_data.pkl", "wb") as f:
            pickle.dump({
                "documents": self.documents,
                "chunks": self.chunks,
                "corpus": [d.tokens for d in self.documents],
            }, f)

    def _load_impl(self, path: str) -> None:
        from rank_bm25 import BM25Okapi
        path = Path(path)
        with open(path / "bm25_data.pkl", "rb") as f:
            data = pickle.load(f)
        self.documents = data["documents"]
        self.chunks = data["chunks"]
        self.bm25 = BM25Okapi(data["corpus"])
