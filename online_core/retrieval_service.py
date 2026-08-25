"""retrieval_service：知识库问答线统一检索服务（D02）。

链路：query 解析 → 难度分档 → 词典应用 → 混合检索（RRF）→ rerank → 父子召回。
供知识库问答线与 Tool Agent（kb_retrieval 工具）共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from offline_core.store import QdrantStore, QdrantConfig
from offline_core.embedder import HuggingFaceEmbeddingModel
from offline_core.retriever import HybridMethod
from online_core.query_parser import parse_query
from online_core.difficulty import estimate
from online_core.lexicon_service import apply_user_lexicon

RAG1_ROOT = Path("D:/个人/Research/RAG1.0")
DEFAULT_EMBEDDING = str(RAG1_ROOT / "local_model/bge-base-zh")
DEFAULT_RERANKER = str(RAG1_ROOT / "local_model/bge-reranker-v2-m3")


@dataclass
class RetrievalConfig:
    index_path: str = str(RAG1_ROOT / "data/indices/法律/qdrant")
    collection: str = "chunks"
    embedding_model: str = DEFAULT_EMBEDDING
    reranker_model: str = DEFAULT_RERANKER
    device: str = "cuda"
    enable_rerank: bool = False  # M0 关闭：4GB 显卡可用显存不足且 CPU 长文本 rerank 过慢；M2 量化/裁剪候选后重开
    recall_top_k: int = 50


@dataclass
class RetrievalOutput:
    query: str
    parsed: dict
    difficulty: dict
    results: list = field(default_factory=list)
    trace: dict = field(default_factory=dict)


class RetrievalService:
    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()
        self._embedding: Optional[HuggingFaceEmbeddingModel] = None
        self._store: Optional[QdrantStore] = None
        self._method: Optional[HybridMethod] = None
        self._reranker = None

    def _get_embedding(self):
        if self._embedding is None:
            # embedding 放 CPU：编码单条 query <1s；GPU 留给 reranker（两模型同占 GPU 会显存争抢卡死）
            self._embedding = HuggingFaceEmbeddingModel(model_name=self.config.embedding_model, device="cpu")
        return self._embedding

    def _get_store(self):
        if self._store is None:
            emb = self._get_embedding()
            cfg = QdrantConfig(
                mode="embedded",
                path=self.config.index_path,
                collection_name=self.config.collection,
                dense_dimension=emb.dimension,
                enable_sparse=True,
            )
            self._store = QdrantStore(cfg)
            self._store._load_impl(self.config.index_path)
        return self._store

    def _get_method(self):
        if self._method is None:
            self._method = HybridMethod(self._get_store(), self._get_embedding(), mode="hybrid")
        return self._method

    def _get_reranker(self):
        if self._reranker is None and self.config.enable_rerank:
            from online_core.reranker import CrossEncoderReranker
            # GTX 1650 4GB 实测可用显存仅约 1.2GB，FP32 reranker 放不下；CPU 30 对约 10s 可接受
            self._reranker = CrossEncoderReranker(model_path=self.config.reranker_model, device="cpu", use_fp16=False)
        return self._reranker

    def search(self, query: str) -> RetrievalOutput:
        # 1. query 解析
        pq = parse_query(query)
        # 2. 难度分档
        diff = estimate(query)
        # 3. 用户词典（查询期）
        apply_user_lexicon()
        # 4. 构造 Qdrant filter（元数据字段位于 payload.metadata 下，用嵌套 key）
        filters = None
        if pq.filter:
            must = []
            for k, v in pq.filter.items():
                if k == "effect_level":
                    # M0 未填充 effect_level 元数据，仅记录不强制过滤
                    continue
                if k == "article_no":
                    # chunk 内含多条文，用 articles 列表 + any 匹配
                    must.append({"key": "metadata.articles", "match": {"any": v if isinstance(v, list) else [v]}})
                    continue
                key = f"metadata.{k}" if k in ("law_name", "article_no", "effect_level", "doc_type") else k
                if isinstance(v, list):
                    must.append({"key": key, "match": {"any": v}})
                else:
                    must.append({"key": key, "match": {"value": v}})
            filters = {"must": must}
        # 5. 混合检索（拆开 dense/sparse，暴露中间结果供 trace 展示）
        store = self._get_store()
        emb = self._get_embedding()
        qvec = emb.embed_texts([query])[0]
        top_k = self.config.recall_top_k
        from offline_core.store import SearchQuery
        dense_sq = SearchQuery(text=query, dense_vector=qvec, mode="dense", filters=filters)
        sparse_sq = SearchQuery(text=query, mode="sparse", filters=filters)
        dense_results = store.search(dense_sq, top_k=top_k)
        sparse_results = store.search(sparse_sq, top_k=top_k)
        raw = HybridMethod._rrf_fuse([dense_results, sparse_results], top_k)
        import jieba
        bm25_tokens = jieba.lcut(query)
        dense_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:80]} for r in dense_results[:10]]
        bm25_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:80]} for r in sparse_results[:10]]
        # 6. rerank（exact_match 跳过精排：精确法条号查询候选已高度相关，且 rerank 在低显存机器极慢）
        results = raw
        if self.config.enable_rerank and not pq.exact_match:
            try:
                reranker = self._get_reranker()
                if reranker is not None:
                    results = reranker.rerank(query, raw[:30], top_k=diff["top_k"])
            except Exception:
                results = raw[: diff["top_k"]]
        else:
            results = raw[: diff["top_k"]]
        # 7. 父子召回：命中 child → 返回 parent（M0：service 层标记，上层决定取 parent 文本）
        trace = {
            "parsed": {
                "law_name": pq.law_name,
                "article_no": pq.article_no,
                "effect_level": pq.effect_level,
                "filter": pq.filter,
                "exact_match": pq.exact_match,
                "excluded": pq.excluded,
            },
            "difficulty": diff,
            "bm25_tokens": bm25_tokens,
            "dense_topk": dense_topk,
            "bm25_topk": bm25_topk,
            "rrf_raw_count": len(raw),
            "final_count": len(results),
        }
        return RetrievalOutput(query=query, parsed=pq, difficulty=diff, results=results, trace=trace)


_service: Optional[RetrievalService] = None


def get_retrieval_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service
