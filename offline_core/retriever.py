"""检索器模块：Dense / BM25 / Hybrid 三种检索模式。

迁移至 Qdrant 后：
- DenseRetriever  → QdrantStore.search(SearchQuery(mode="dense"))
- BM25Retriever   → QdrantStore.search(SearchQuery(mode="sparse"))
- HybridRetriever → QdrantStore.search(SearchQuery(mode="hybrid"))

策略层（新）：使用 HybridMethod 作为底层引擎，通过 Strategy 封装检索空间逻辑。
"""

import warnings
from abc import ABC, abstractmethod
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from offline_core.data_model import Chunk, RetrievalResult, SearchQuery
from offline_core.modules import BaseRetriever, BaseEmbeddingModel
from offline_core.store import QdrantStore


# ══════════════════════════════════════════════════════════════════════════════
# 新：方法层 (Method) —— 底层匹配引擎
# ══════════════════════════════════════════════════════════════════════════════


class HybridMethod:
    """底层检索引擎。

    封装 QdrantStore 检索（dense / sparse / hybrid），
    对外提供统一的 search 接口，各类 Strategy 内部使用。

    用法:
        method = HybridMethod(store, embedding_model)           # 默认 hybrid
        method = HybridMethod(store, embedding_model, mode="dense")
        results = method.search("违约责任", top_k=10)
        results = method.search("违约责任", top_k=10, filters={...})
    """

    def __init__(self, store: QdrantStore, embedding_model: BaseEmbeddingModel,
                 mode: str = "hybrid"):
        self.store = store
        self.embedding_model = embedding_model
        self.mode = mode  # "dense" | "sparse" | "hybrid"

    def search(
        self,
        text: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """执行检索。

        hybrid 模式下，dense + sparse 通过 ThreadPoolExecutor 并行执行，
        再对结果做 RRF 融合，减少 Qdrant 串行处理开销。

        Args:
            text: 查询文本
            top_k: 返回结果数
            filters: Qdrant Filter 条件（可选），格式如 {"must": [...]}

        Returns:
            检索结果列表
        """
        query_vector = self.embedding_model.embed_texts([text])[0]

        if self.mode != "hybrid":
            # 单路模式：直接交给 Qdrant
            search_query = SearchQuery(
                text=text,
                dense_vector=query_vector,
                mode=self.mode,
                filters=filters,
            )
            return self.store.search(search_query, top_k=top_k)

        # hybrid 模式：并行 dense + sparse 检索，手动 RRF 融合
        dense_sq = SearchQuery(
            text=text, dense_vector=query_vector,
            mode="dense", filters=filters,
        )
        sparse_sq = SearchQuery(
            text=text, mode="sparse", filters=filters,
        )

        store = self.store
        with ThreadPoolExecutor(max_workers=2) as ex:
            dense_future = ex.submit(store.search, dense_sq, top_k)
            sparse_future = ex.submit(store.search, sparse_sq, top_k)

        dense_results = dense_future.result()
        sparse_results = sparse_future.result()

        return self._rrf_fuse([dense_results, sparse_results], top_k=top_k)

    @staticmethod
    def _rrf_fuse(
        retrieval_lists: list[list[RetrievalResult]],
        top_k: int = 10,
        k: int = 60,
    ) -> list[RetrievalResult]:
        """RRF 融合多个检索结果列表。"""
        scores: dict[str, float] = defaultdict(float)
        chunk_map: dict[str, "Chunk"] = {}
        for results in retrieval_lists:
            for rank, r in enumerate(results):
                cid = r.chunk.chunk_id
                chunk_map[cid] = r.chunk
                scores[cid] += 1.0 / (k + rank + 1)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            RetrievalResult(chunk=chunk_map[cid], score=score, retrieval_type="rrf_fusion")
            for cid, score in ranked[:top_k]
        ]


# ══════════════════════════════════════════════════════════════════════════════
# 新：策略层 (Strategy) —— 定义检索空间
# ══════════════════════════════════════════════════════════════════════════════


class BaseStrategy(ABC):
    """策略基类。

    策略不关心底层匹配方式（永远是 hybrid），
    它只定义"在哪个空间里检索"。

    所有策略共享一个 HybridMethod 实例。
    """

    def __init__(self, method: HybridMethod):
        self.method = method

    @abstractmethod
    def execute(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        ...


class SimpleStrategy(BaseStrategy):
    """全文空间 hybrid 检索，不做任何过滤。"""

    def execute(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        return self.method.search(query, top_k)


class FilterStrategy(BaseStrategy):
    """元数据过滤后的子空间 hybrid 检索。

    用法:
        method = HybridMethod(store, model)
        strategy = FilterStrategy(
            method,
            filters={"must": [{"key": "metadata.doc_type", "match": {"value": "法律"}}]}
        )
        results = strategy.execute("违约责任")
    """

    def __init__(self, method: HybridMethod, filters: dict):
        super().__init__(method)
        self.filters = filters

    def execute(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        return self.method.search(query, top_k, filters=self.filters)


class HierarchicalStrategy(BaseStrategy):
    """两阶段层次检索：摘要粗筛 → chunk 细搜。

    Stage 1: 在摘要点（chunk_level="document"）中 hybrid 检索，提取命中文档 ID
    Stage 2: 在 Stage 1 命中文档的 chunk 中 hybrid 检索
    """

    def __init__(self, method: HybridMethod, store: QdrantStore):
        super().__init__(method)
        self.store = store

    def execute(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        # Stage 1: 摘要层粗筛
        summary_results = self.method.search(
            query,
            top_k=top_k,
            filters={
                "must": [
                    {"key": "chunk_level", "match": {"value": "document"}}
                ]
            },
        )
        doc_ids = [r.chunk.doc_id for r in summary_results if r.chunk.doc_id]

        if not doc_ids:
            return []

        # Stage 2: 在命中文档的 chunk 中细搜
        return self.method.search(
            query,
            top_k=top_k,
            filters={
                "must": [
                    {"key": "doc_id", "match": {"any": doc_ids}}
                ]
            },
        )


class ParentChildStrategy(BaseStrategy):
    """Parent-Child 两阶段检索策略。

    核心思想：child chunk（细粒度，~250 chars）负责精确匹配，
            parent chunk（粗粒度，~1000 chars）负责提供完整上下文。

    流程:
        Stage 1 — 在 child 空间中 hybrid 检索，找到细粒度匹配
        Stage 2 — 从匹配结果中提取 parent_chunk_id，回查 parent chunks
        Stage 3 — 每个 parent 取其 children 的最高分，按分排序返回
    """

    def __init__(self, method: HybridMethod, store: QdrantStore):
        super().__init__(method)
        self.store = store

    def execute(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        # Stage 1: child 空间检索（多搜一些 child，确保 parent 覆盖充足）
        child_top_k = max(top_k * 2, 50)
        child_results = self.method.search(
            query,
            top_k=child_top_k,
            filters={
                "must": [
                    {"key": "chunk_level", "match": {"value": "child"}}
                ]
            },
        )
        if not child_results:
            return []

        # Stage 2: 提取 parent_chunk_id → 去重 → 回查
        parent_ids = list(set(
            r.chunk.parent_chunk_id
            for r in child_results
            if r.chunk.parent_chunk_id
        ))
        if not parent_ids:
            return []

        parent_map = self.store.get_chunks_by_ids(parent_ids)
        if not parent_map:
            return []

        # Stage 3: 每个 parent 继承其 children 的最高分
        parent_scores: dict[str, float] = {}
        for r in child_results:
            pid = r.chunk.parent_chunk_id
            if pid and pid in parent_map:
                if pid not in parent_scores or r.score > parent_scores[pid]:
                    parent_scores[pid] = r.score

        # 按分降序排列，取 top_k
        sorted_parents = sorted(
            parent_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

        return [
            RetrievalResult(
                chunk=parent_map[pid],
                score=score,
                retrieval_type="parent_child",
            )
            for pid, score in sorted_parents
        ]


# ══════════════════════════════════════════════════════════════════════════════
# 旧：Retriever 类（已废弃，请使用策略层 HybridMethod + Strategy）
# ══════════════════════════════════════════════════════════════════════════════


class DenseRetriever(BaseRetriever):
    """稠密向量检索器。

    接受 QdrantStore（推荐）或旧版 FAISSStore（已废弃）。

    ⚠️ 已废弃：请使用策略层 HybridMethod + SimpleStrategy。
    """

    def __init__(self, store, embedding_model: BaseEmbeddingModel):
        warnings.warn(
            "DenseRetriever 已废弃，请使用策略层 HybridMethod + SimpleStrategy。"
            "详情见 openspec/changes/advanced-retrieval-online-architecture/",
            DeprecationWarning,
            stacklevel=2,
        )
        self.store = store
        self.embedding_model = embedding_model
        self._is_qdrant = isinstance(store, QdrantStore)

    def _retrieve_impl(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        query_vector = self.embedding_model.embed_texts([query])[0]

        if self._is_qdrant:
            search_query = SearchQuery(
                text=query,
                dense_vector=query_vector,
                mode="dense",
            )
            return self.store.search(search_query, top_k=top_k)
        else:
            # 旧版 FAISSStore / InMemoryStore
            return self.store.search(query_vector, top_k=top_k)


class BM25Retriever(BaseRetriever):
    """BM25 关键词检索器（稀疏向量）。

    接受 QdrantStore（推荐，使用原生 Sparse Vector）或旧版 BM25Store（已废弃）。

    ⚠️ 已废弃：请使用策略层 HybridMethod + SimpleStrategy。
    """

    def __init__(self, store):
        warnings.warn(
            "BM25Retriever 已废弃，请使用策略层 HybridMethod + SimpleStrategy。"
            "详情见 openspec/changes/advanced-retrieval-online-architecture/",
            DeprecationWarning,
            stacklevel=2,
        )
        self.store = store
        self._is_qdrant = isinstance(store, QdrantStore)

    def _retrieve_impl(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        if self._is_qdrant:
            search_query = SearchQuery(text=query, mode="sparse")
            return self.store.search(search_query, top_k=top_k)
        else:
            # 旧版 BM25Store
            return self.store.search(query, top_k=top_k)


class HybridRetriever(BaseRetriever):
    """混合检索器（Dense + Sparse）。

    QdrantStore 模式下：使用 Qdrant 原生多向量检索 + 内置融合（RRF/DBSF）。
    旧版模式下：回退到手动 RRF 融合。

    ⚠️ 已废弃：请使用策略层 HybridMethod + SimpleStrategy。
    """

    def __init__(self, retrievers: list[BaseRetriever], fusion=None):
        warnings.warn(
            "HybridRetriever 已废弃，请使用策略层 HybridMethod + SimpleStrategy。"
            "详情见 openspec/changes/advanced-retrieval-online-architecture/",
            DeprecationWarning,
            stacklevel=2,
        )
        self.retrievers = retrievers
        self.fusion = fusion

        # 检测是否为 Qdrant 统一模式（单个 QdrantStore 即可完成 hybrid）
        self._is_unified_qdrant = (
            len(retrievers) == 1
            and isinstance(retrievers[0], DenseRetriever)
            and retrievers[0]._is_qdrant
        )

        if fusion is not None and self._is_unified_qdrant:
            # Qdrant 原生融合不需要外部 RRFFusion，但保留参数兼容
            pass

    def _retrieve_impl(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        if self._is_unified_qdrant:
            # Qdrant 原生 hybrid：通过单个 DenseRetriever 的 store 完成
            dense_retriever = self.retrievers[0]
            query_vector = dense_retriever.embedding_model.embed_texts([query])[0]
            search_query = SearchQuery(
                text=query,
                dense_vector=query_vector,
                mode="hybrid",
            )
            return dense_retriever.store.search(search_query, top_k=top_k)
        else:
            # 旧版手动 RRF 融合
            return self._legacy_hybrid(query, top_k)

    def _legacy_hybrid(self, query: str, top_k: int) -> list[RetrievalResult]:
        """旧版手动 RRF 融合（兼容旧 FAISSStore + BM25Store 组合）。"""
        retrieval_lists = []
        for retriever in self.retrievers:
            results = retriever.retrieve(query, top_k=top_k)
            retrieval_lists.append(results)
        return self._rrf_fuse(retrieval_lists)[:top_k]

    @staticmethod
    def _rrf_fuse(retrieval_lists: list[list[RetrievalResult]], k: int = 60) -> list[RetrievalResult]:
        """手动 RRF 融合（向后兼容）。"""
        scores = defaultdict(float)
        chunk_map = {}
        for results in retrieval_lists:
            for rank, result in enumerate(results):
                chunk_id = result.chunk.chunk_id
                chunk_map[chunk_id] = result.chunk
                rrf_score = 1 / (k + rank + 1)
                scores[chunk_id] += rrf_score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            RetrievalResult(
                chunk=chunk_map[cid],
                score=score,
                retrieval_type="rrf_fusion",
            )
            for cid, score in ranked
        ]


class RRFFusion:
    """⚠️ 已废弃。Qdrant 原生支持 RRF 融合，不再需要此类。

    保留用于兼容旧代码。
    """

    def __init__(self, k=60):
        warnings.warn(
            "RRFFusion 已废弃，Qdrant 原生支持 RRF 融合。",
            DeprecationWarning,
            stacklevel=2,
        )
        self.k = k

    def fuse(self, retrieval_lists: list[list[RetrievalResult]]) -> list[RetrievalResult]:
        return HybridRetriever._rrf_fuse(retrieval_lists, k=self.k)


def get_retriever(
    store=None,
    config=None,
    embedding_model: Optional[BaseEmbeddingModel] = None,
    fusion=None,
) -> BaseRetriever:
    """工厂函数：按配置创建检索器。

    迁移后推荐使用 QdrantStore + 单一 store 实例：
        store = QdrantStore(config)
        retriever = get_retriever(store=store, config=config, embedding_model=model)

    Args:
        store: QdrantStore（推荐）或旧版 FAISSStore/BM25Store
        config: PipelineConfig 或具有 .mode 属性的配置对象
        embedding_model: embedding 模型实例
        fusion: （废弃）RRFFusion，QdrantStore 下不再需要

    Returns:
        BaseRetriever 子类实例
    """
    is_qdrant = isinstance(store, QdrantStore)

    if config.mode == "dense":
        if store is None or embedding_model is None:
            raise ValueError("DenseRetriever requires store and embedding_model")
        return DenseRetriever(store, embedding_model)

    elif config.mode == "bm25":
        if store is None:
            raise ValueError("BM25Retriever requires store")
        return BM25Retriever(store)

    elif config.mode == "hybrid":
        if store is None or embedding_model is None:
            raise ValueError("HybridRetriever requires store and embedding_model")

        if is_qdrant:
            # Qdrant 原生 hybrid：仅需 DenseRetriever（内部通过 SearchQuery.mode="hybrid" 实现）
            dense = DenseRetriever(store, embedding_model)
            return HybridRetriever([dense])
        else:
            # 旧版：Dense + BM25 双路
            if fusion is None:
                raise ValueError("HybridRetriever (legacy) requires fusion")
            dense = DenseRetriever(store, embedding_model)
            bm25 = BM25Retriever(store)
            return HybridRetriever([dense, bm25], fusion)

    elif config.mode == "parent_child":
        raise NotImplementedError(
            "Parent-Child 检索器将在后续版本中基于 Qdrant 实现。"
        )

    else:
        raise ValueError(f"Unsupported retrieval mode: {config.mode}")
