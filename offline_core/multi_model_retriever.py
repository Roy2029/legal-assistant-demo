"""多模型 Ensemble 检索器 — 多路 Dense + BM25 RRF 融合。

用法：
    retriever = MultiModelRetriever(kb_dir="data/indices/法律")
    results = retriever.retrieve("劳动争议如何处理？", top_k=20)
    # 或批量
    results_map = retriever.batch_retrieve(["query1", "query2"], top_k=20)
"""

import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

import torch

from offline_core.data_model import RetrievalResult, SearchQuery

logger = logging.getLogger(__name__)


class MultiModelRetriever:
    """多模型 + BM25 混合检索器，使用 RRF 融合多路召回结果。

    架构：
        bge-base-zh (768-dim, dense) ──┐
        bge-m3      (1024-dim, dense) ─┤
        qwen3-emb   (1024-dim, dense) ─┼── RRF ── top-k
        BM25        (sparse)          ─┘

    实现了与 RelevanceLabeler 兼容的 RetrieverProtocol 接口。
    """

    def __init__(self, kb_dir: str, device: str = "cuda", rrf_k: int = 60):
        """
        Args:
            kb_dir: 知识库目录（如 data/indices/法律），内含 manifest.json 和各 qdrant_*/ 子目录
            device: 模型运行设备（"cuda" 或 "cpu"）
            rrf_k: RRF 融合参数 k（默认 60）
        """
        self.kb_dir = Path(kb_dir)
        self.device = device
        self.rrf_k = rrf_k

        # 从 manifest 读取可用索引
        manifest_path = self.kb_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.json 不存在: {manifest_path}")

        from offline_core.manifest import Manifest
        self.manifest = Manifest.load(manifest_path)
        self.indices_info = self.manifest.get_available_indices()

        if not self.indices_info:
            raise ValueError(f"manifest 中无可用索引: {manifest_path}")

        logger.info("MultiModelRetriever: 发现 %d 个模型索引", len(self.indices_info))
        for model_name, info in self.indices_info.items():
            logger.info("  • %s → %s (dim=%d, chunks=%d)",
                        model_name, info["qdrant_subdir"],
                        info["dimension"], info.get("total_chunks", 0))

        # 每个模型的 QdrantStore（延迟初始化）
        self._stores: dict[str, "QdrantStore"] = {}
        self._models: dict[str, "HuggingFaceEmbeddingModel"] = {}
        self._bm25_store: Optional["QdrantStore"] = None
        self._bm25_model_name: Optional[str] = None

    # ── 公开接口 ─────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        """单 query 检索（兼容 RetrieverProtocol）。

        注意：单 query 调用时每个模型加载一次后立即卸载。批量场景请用 batch_retrieve()。
        """
        all_results: list[list[RetrievalResult]] = []

        for model_name in self.indices_info:
            dense_results = self._dense_search(model_name, query, top_k)
            if dense_results:
                all_results.append(dense_results)

        # BM25
        bm25_results = self._bm25_search(query, top_k)
        if bm25_results:
            all_results.append(bm25_results)

        if not all_results:
            return []
        if len(all_results) == 1:
            return all_results[0][:top_k]

        return self._rrf_fuse(all_results, top_k)

    def batch_retrieve(self, queries: list[str], top_k: int = 20) -> dict[str, list[RetrievalResult]]:
        """批量检索（优化：每个模型只加载一次，批量编码所有 query）。

        Args:
            queries: query 文本列表
            top_k: 每个 query 从每路检索的候选数

        Returns:
            {query_text: [RetrievalResult, ...]}  经过 RRF 融合后的最终 top-k 结果
        """
        if not queries:
            return {}

        # 收集每路检索结果: query → [list of result lists]
        raw_results: dict[str, list[list[RetrievalResult]]] = defaultdict(list)

        # Phase 1: 每个模型批量处理所有 query
        for model_name in self.indices_info:
            logger.info("批量检索 [%s]: 编码 %d 条 query...", model_name, len(queries))
            model = self._get_model(model_name)
            query_vecs = model.embed_texts(queries)

            store = self._get_store(model_name)
            for i, (q, vec) in enumerate(zip(queries, query_vecs)):
                sq = SearchQuery(text=q, dense_vector=vec, mode="dense")
                results = store.search(sq, top_k=top_k)
                if results:
                    raw_results[q].append(results)

            # 释放 GPU 显存
            del model, query_vecs
            self._models.pop(model_name, None)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        # Phase 2: BM25 检索
        for q in queries:
            bm25_results = self._bm25_search(q, top_k)
            if bm25_results:
                raw_results[q].append(bm25_results)

        # Phase 3: RRF 融合
        fused: dict[str, list[RetrievalResult]] = {}
        for q, result_lists in raw_results.items():
            if len(result_lists) == 1:
                fused[q] = result_lists[0][:top_k]
            else:
                fused[q] = self._rrf_fuse(result_lists, top_k)

        total_retrieval_paths = sum(len(v) for v in raw_results.values())
        logger.info("批量检索完成: %d queries × avg %.1f retrieval paths → %d fused results",
                    len(queries), total_retrieval_paths / len(queries) if queries else 0,
                    sum(len(v) for v in fused.values()))
        return fused

    # ── 内部方法 ─────────────────────────────────────────────────

    def _get_store(self, model_name: str) -> "QdrantStore":
        """获取或创建指定模型的 QdrantStore（延迟加载）。"""
        if model_name not in self._stores:
            from offline_core.store import QdrantStore, QdrantConfig, BM25Encoder
            info = self.indices_info[model_name]
            qdrant_path = str(self.kb_dir / info["qdrant_subdir"])

            is_bm25_source = (model_name == self._get_bm25_model_name())
            config = QdrantConfig(
                mode="embedded",
                path=qdrant_path,
                collection_name="chunks",
                dense_dimension=info["dimension"],
                enable_sparse=is_bm25_source,
            )
            store = QdrantStore(config)

            # 手动恢复 BM25 encoder（QdrantStore 需 _load_impl 但无公共 load）
            if is_bm25_source:
                encoder_path = str(self.kb_dir / info["qdrant_subdir"] / "bm25_encoder.pkl")
                bm25_path = Path(encoder_path)
                if bm25_path.exists():
                    store._bm25_encoder = BM25Encoder.load(encoder_path)
                    store._collection_ready = False  # 触发 collection 验证
                    store._indexes_created = True

            self._stores[model_name] = store

        return self._stores[model_name]

    def _get_model(self, model_name: str) -> "HuggingFaceEmbeddingModel":
        """获取或创建指定名称的 embedding 模型（延迟加载）。"""
        if model_name not in self._models:
            from offline_core.embedder import HuggingFaceEmbeddingModel
            model = HuggingFaceEmbeddingModel(
                model_name=model_name,
                device=self.device,
            )
            self._models[model_name] = model
        return self._models[model_name]

    def _get_bm25_model_name(self) -> Optional[str]:
        """返回负责 BM25 的模型名（第一个注册的模型）。"""
        if self._bm25_model_name:
            return self._bm25_model_name
        for model_name in self.indices_info:
            self._bm25_model_name = model_name
            return model_name
        return None

    def _dense_search(self, model_name: str, query: str, top_k: int) -> list[RetrievalResult]:
        """用指定模型做 dense 检索。"""
        try:
            model = self._get_model(model_name)
            query_vec = model.embed_texts([query])[0]
            store = self._get_store(model_name)
            sq = SearchQuery(text=query, dense_vector=query_vec, mode="dense")
            return store.search(sq, top_k=top_k)
        except Exception as e:
            logger.warning("Dense search failed for %s: %s", model_name, e)
            return []

    def _get_bm25_store(self) -> Optional["QdrantStore"]:
        """获取可用于 BM25 检索的 store。

        优先级：
        1. 第一个模型目录下的 BM25（多模型构建时一并创建）
        2. kb_dir 下的 qdrant/ 旧目录（fallback — 旧版单模型构建产物）
        """
        if self._bm25_store is not None:
            return self._bm25_store

        # 1. 尝试第一个注册的模型
        bm25_model = self._get_bm25_model_name()
        if bm25_model:
            store = self._get_store(bm25_model)
            if store.config.enable_sparse and store._bm25_encoder is not None:
                self._bm25_store = store
                logger.info("BM25: 使用 %s 的 sparse 索引", bm25_model)
                return store

        # 2. Fallback — 旧版 qdrant/ 目录
        legacy_path = self.kb_dir / "qdrant"
        if legacy_path.is_dir() and (legacy_path / "bm25_encoder.pkl").exists():
            from offline_core.store import QdrantStore, QdrantConfig, BM25Encoder
            config = QdrantConfig(
                mode="embedded",
                path=str(legacy_path),
                collection_name="chunks",
                dense_dimension=768,  # 仅用于兼容旧索引
                enable_sparse=True,
            )
            store = QdrantStore(config)
            store._bm25_encoder = BM25Encoder.load(str(legacy_path / "bm25_encoder.pkl"))
            self._bm25_store = store
            logger.info("BM25: 使用旧版 qdrant/ 目录的 sparse 索引 (fallback)")
            return store

        return None

    def _bm25_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """BM25 稀疏检索。"""
        try:
            store = self._get_bm25_store()
            if store is None:
                return []
            sq = SearchQuery(text=query, mode="sparse")
            return store.search(sq, top_k=top_k)
        except Exception as e:
            logger.debug("BM25 search failed: %s", e)
            return []

    def _rrf_fuse(self, retrieval_lists: list[list[RetrievalResult]],
                  top_k: int) -> list[RetrievalResult]:
        """RRF (Reciprocal Rank Fusion) 融合多路检索结果。

        与 HybridRetriever._rrf_fuse 相同算法。
        """
        scores: dict[str, float] = defaultdict(float)
        chunk_map: dict[str, "Chunk"] = {}

        for results in retrieval_lists:
            for rank, result in enumerate(results):
                chunk_id = result.chunk.chunk_id
                if chunk_id not in chunk_map:
                    chunk_map[chunk_id] = result.chunk
                scores[chunk_id] += 1.0 / (self.rrf_k + rank + 1)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            RetrievalResult(
                chunk=chunk_map[cid],
                score=score,
                retrieval_type="rrf_fusion",
            )
            for cid, score in ranked[:top_k]
        ]
