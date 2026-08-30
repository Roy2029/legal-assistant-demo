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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAG1_ROOT = Path("D:/个人/Research/RAG1.0")
# 打包后模型位于 {安装目录}/models/bge-base-zh；开发机回退到 RAG1 原路径
_PORTABLE_EMBEDDING = PROJECT_ROOT / "models" / "bge-base-zh"
DEFAULT_EMBEDDING = str(_PORTABLE_EMBEDDING if _PORTABLE_EMBEDDING.exists() else RAG1_ROOT / "local_model/bge-base-zh")
DEFAULT_RERANKER = str(RAG1_ROOT / "local_model/bge-reranker-v2-m3")


@dataclass
class RetrievalConfig:
    index_path: str = str(Path("D:/个人/legal-assistant-demo/data/indices/法律/qdrant"))
    collection: str = "chunks"
    embedding_model: str = DEFAULT_EMBEDDING
    embedding_device: str = "cpu"  # 普通用户默认 CPU；embedding 与索引构建同模型，CPU 单条查询 <1s
    reranker_model: str = DEFAULT_RERANKER
    reranker_provider: str = "skip"  # skip | local | api
    reranker_api_url: str = ""       # api provider 的 rerank 端点（OpenAI 兼容 /rerank）
    reranker_api_key: str = ""       # api provider 的密钥；为空时回退 LLM_API_KEY
    reranker_api_model: str = "bge-reranker-v2-m3"  # 云上 rerank 模型名
    device: str = "cpu"  # 兼容旧字段：统一默认 CPU，避免低显存机器 OOM
    enable_rerank: bool = False  # M0 关闭：4GB 显卡可用显存不足且 CPU 长文本 rerank 过慢；M2 量化/裁剪候选后重开
    recall_top_k: int = 50


@dataclass
class RetrievalOutput:
    query: str
    parsed: dict
    difficulty: dict
    results: list = field(default_factory=list)
    trace: dict = field(default_factory=dict)


def _corpus_public_condition() -> dict:
    """公共库 corpus 条件：corpus=="public" 或 corpus 字段缺失/为空。

    历史公共 chunk 由旧重建脚本生成，未写 corpus 标签（仅 law_name/article_no/articles/doc_type）；
    公共作用域必须同时命中已正确打标与缺失标签的 chunk，且不得命中 user/case 库 chunk。
    `is_empty` 为 Qdrant 独立条件：字段缺失、null 或空数组均命中。
    """
    return {
        "should": [
            {"key": "metadata.corpus", "match": {"value": "public"}},
            {"is_empty": {"key": "metadata.corpus"}},
        ]
    }


def build_kb_filters(
    corpus_scope: str = "all",
    user_folders: Optional[list[str]] = None,
    folders: Optional[list[str]] = None,
    pq_filter: Optional[dict] = None,
) -> Optional[dict]:
    """构造知识库范围 + 解析器过滤条件。

    folders 优先级最高：[] = 全部；["__public__"] = 公共库；
    ["a"] = 用户文件夹；["__public__","a"] = 公共库 OR 指定用户文件夹。
    """
    filters = None
    must = []
    base_should = None

    if folders is not None:
        public_selected = "__public__" in folders
        user_selected = [f for f in folders if f != "__public__"]
        if public_selected and user_selected:
            base_should = [
                _corpus_public_condition(),
                {
                    "must": [
                        {"key": "metadata.corpus", "match": {"value": "user"}},
                        {"key": "metadata.user_id", "match": {"value": "local"}},
                        {"key": "metadata.folder", "match": {"any": user_selected}},
                    ]
                },
            ]
        elif public_selected:
            must.append(_corpus_public_condition())
        elif user_selected:
            must.append({"key": "metadata.corpus", "match": {"value": "user"}})
            must.append({"key": "metadata.user_id", "match": {"value": "local"}})
            must.append({"key": "metadata.folder", "match": {"any": user_selected}})
    elif user_folders:
        must.append({"key": "metadata.corpus", "match": {"value": "user"}})
        must.append({"key": "metadata.user_id", "match": {"value": "local"}})
        must.append({"key": "metadata.folder", "match": {"any": list(user_folders)}})
    elif corpus_scope == "public":
        must.append(_corpus_public_condition())
    elif corpus_scope == "user":
        must.append({"key": "metadata.corpus", "match": {"value": "user"}})
        must.append({"key": "metadata.user_id", "match": {"value": "local"}})

    if pq_filter:
        for k, v in pq_filter.items():
            if k == "effect_level":
                continue
            if k == "article_no":
                must.append({"key": "metadata.articles", "match": {"any": v if isinstance(v, list) else [v]}})
                continue
            key = f"metadata.{k}" if k in ("law_name", "article_no", "effect_level", "doc_type") else k
            if isinstance(v, list):
                must.append({"key": key, "match": {"any": v}})
            else:
                must.append({"key": key, "match": {"value": v}})

    if base_should is not None:
        filters = {"should": base_should}
        if must:
            filters["must"] = must
    elif must:
        filters = {"must": must}
    return filters


class RetrievalService:
    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()
        self._embedding: Optional[HuggingFaceEmbeddingModel] = None
        self._store: Optional[QdrantStore] = None
        self._method: Optional[HybridMethod] = None
        self._reranker = None

    def close(self):
        """关闭底层 QdrantClient，释放嵌入式 Qdrant 文件锁（供重配/测试复用）。"""
        try:
            if self._store is not None:
                self._store.close()
        except Exception:
            pass
        self._store = None
        self._method = None
        self._reranker = None

    def _get_embedding(self):
        if self._embedding is None:
            # embedding 默认 CPU（D02 §8.2 接口方案）：编码单条 query <1s；
            # 索引构建用 GPU batch 已完成，查询期 CPU 足够，避免两模型争抢显存。
            device = self.config.embedding_device or "cpu"
            self._embedding = HuggingFaceEmbeddingModel(model_name=self.config.embedding_model, device=device)
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
            provider = (self.config.reranker_provider or "skip").lower()
            if provider == "api":
                from online_core.reranker import APIReranker
                self._reranker = APIReranker(
                    api_url=self.config.reranker_api_url,
                    api_key=self.config.reranker_api_key,
                    model=self.config.reranker_api_model,
                )
            elif provider == "local":
                from online_core.reranker import CrossEncoderReranker
                # GTX 1650 4GB 实测可用显存仅约 1.2GB，FP32 reranker 放不下；CPU 30 对约 10s 可接受
                self._reranker = CrossEncoderReranker(model_path=self.config.reranker_model, device="cpu", use_fp16=False)
            else:
                self._reranker = None
        return self._reranker

    def search(
        self,
        query: str,
        corpus_scope: str = "all",
        user_folders: Optional[list[str]] = None,
        folders: Optional[list[str]] = None,
    ) -> RetrievalOutput:
        """corpus_scope: all（public+本人 user）/ public / user。

        user_folders: 只检索这些用户文件夹（metadata.folder），传入后覆盖 corpus_scope。
        folders: 统一知识库选择器，优先级高于 corpus_scope/user_folders。
                 [] = 全部；["__public__"] = 公共库；["folderA"] = 用户文件夹；
                 ["__public__","folderA"] = 公共库 OR 指定用户文件夹。
        """
        # 1. query 解析
        pq = parse_query(query)
        # 2. 难度分档
        diff = estimate(query)
        # 3. 用户词典（查询期）
        apply_user_lexicon()
        # 4. 构造 Qdrant filter（元数据字段位于 payload.metadata 下，用嵌套 key）
        filters = build_kb_filters(
            corpus_scope=corpus_scope,
            user_folders=user_folders,
            folders=folders,
            pq_filter=pq.filter or None,
        )
        # 5. 混合检索（拆开 dense/sparse，暴露中间结果供 trace 展示）
        store = self._get_store()
        emb = self._get_embedding()
        qvec = emb.embed_texts([query])[0]
        top_k = self.config.recall_top_k
        from offline_core.store import SearchQuery
        dense_sq = SearchQuery(text=query, dense_vector=qvec, mode="dense", filters=filters)
        sparse_sq = SearchQuery(text=query, mode="sparse", filters=filters)
        # 并行 dense/sparse：Qdrant 嵌入式模式单路查询约 0.5-1s，串行会翻倍
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            dense_future = ex.submit(store.search, dense_sq, top_k)
            sparse_future = ex.submit(store.search, sparse_sq, top_k)
            dense_results = dense_future.result()
            sparse_results = sparse_future.result()
        raw = HybridMethod._rrf_fuse([dense_results, sparse_results], top_k)
        import jieba
        bm25_tokens = jieba.lcut(query)

        def _meta(r):
            m = r.chunk.metadata or {}
            return {
                "law_name": m.get("law_name", ""),
                "article_no": m.get("article_no", ""),
                "chunk_level": r.chunk.chunk_level,
                "corpus": m.get("corpus", ""),
                "folder": m.get("folder", ""),
                "doc_type": m.get("doc_type", ""),
                "heading_path": r.chunk.heading_path or [],
            }

        dense_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:120], "meta": _meta(r)} for r in dense_results[:10]]
        bm25_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:120], "meta": _meta(r)} for r in sparse_results[:10]]
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
        rrf_raw_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:120], "meta": _meta(r)} for r in raw[:10]]
        final_topk = [{"chunk_id": r.chunk.chunk_id, "score": round(float(r.score), 4), "text": r.chunk.text[:120], "meta": _meta(r)} for r in results]
        trace = {
            "rrf_raw_topk": rrf_raw_topk,
            "final_topk": final_topk,
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

    def search_multi(self, queries: list[str], corpus_scope: str = "all") -> RetrievalOutput:
        """多子查询并行检索合并（D02 §8 / M1 knowledge_agent）。"""
        if not queries:
            return RetrievalOutput(query="", parsed={}, difficulty={}, results=[], trace={})
        outs = [self.search(q, corpus_scope=corpus_scope) for q in queries]
        merged = {}
        for o in outs:
            for r in o.results:
                cid = r.chunk.chunk_id
                if cid in merged:
                    merged[cid].score += r.score
                else:
                    merged[cid] = r
        results = sorted(merged.values(), key=lambda x: x.score, reverse=True)[: self.config.recall_top_k]
        return RetrievalOutput(
            query=" | ".join(queries),
            parsed=outs[0].parsed,
            difficulty={"level": "hard", "rule_hit": "multi_query", "top_k": 8},
            results=results,
            trace={"sub_queries": queries, "sub_counts": [len(o.results) for o in outs], "merged_count": len(results)},
        )


_service: Optional[RetrievalService] = None
_configured: bool = False


def configure_retrieval(config: RetrievalConfig) -> RetrievalService:
    """用自定义配置初始化（或重置）全局检索服务单例。

    服务器启动时调用一次，避免各 API 模块各自创建 QdrantClient 导致
    本地嵌入式 Qdrant 的 AlreadyLocked 冲突。
    """
    global _service, _configured
    if _service is not None:
        _service.close()
    _service = RetrievalService(config)
    _configured = True
    return _service


def get_retrieval_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service
