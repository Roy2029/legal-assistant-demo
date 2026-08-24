"""可组合的阶段式评估管线。

实现 EvalPipeline — 支持独立开关各阶段的检索管线，用于消融实验。
对接 StrategyDispatcher、CrossEncoderReranker、QueryRouter/QueryPreFilter。

管线流程:
    默认模式 (router=false):
        RouteDecision.fallback → StrategyDispatcher → [Reranker?] → ranked chunks

    在线模式 (router=true):
        QueryPreFilter → QueryRouter → StrategyDispatcher → [Reranker?] → ranked chunks
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from offline_core.data_model import RetrievalResult
from offline_core.modules import BaseEmbeddingModel
from offline_core.store import QdrantStore
from online_core.data_model import (
    RouteDecision,
    RetrievalResponse,
)
from online_core.strategy_dispatcher import StrategyDispatcher
from online_core.reranker import CrossEncoderReranker
from online_core.query_router import QueryPreFilter, QueryRouter

from evaluation.config import PipelineConfig

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """管线输出的统一 chunk 结构，供指标计算层消费。"""

    chunk_id: str
    score: float
    content: str
    doc_id: str

    @classmethod
    def from_result(cls, r: RetrievalResult) -> "RetrievedChunk":
        return cls(
            chunk_id=r.chunk.chunk_id,
            score=r.score,
            content=r.chunk.text,
            doc_id=r.chunk.doc_id,
        )


@dataclass
class PipelineResult:
    """检索管线的完整结果，包含耗时分解和路由信息。

    供指标计算层消费 chunks，同时让分析工具获取时间开销和路由行为。
    """

    chunks: list[RetrievedChunk]
    timing: dict[str, float] = field(default_factory=dict)
    route_strategies: list[str] = field(default_factory=list)
    route_difficulty: str = ""
    route_decision_detail: str = ""


class EvalPipeline:
    """可组合的阶段式评估管线。

    四个阶段：prefilter → router → recall → rerank。
    每个阶段可独立开关和调参，用于消融实验和控制变量对比。

    用法:
        pipeline = EvalPipeline(config, store, embedding_model, llm=llm)
        chunks = pipeline.retrieve("什么是违约责任", top_k=10)
    """

    def __init__(
        self,
        config: PipelineConfig,
        store: QdrantStore,
        embedding_model: BaseEmbeddingModel,
        llm: object = None,
        fallback_llm: object = None,
        kb_vocab_path: str | None = None,
    ):
        """
        Args:
            config: Pipeline 阶段配置
            store: Qdrant 向量存储
            embedding_model: Embedding 模型实例
            llm: 可选的 LLM 实例（router 启用时必需）
            fallback_llm: 可选的备用 LLM（主 LLM 失败时使用）
            kb_vocab_path: PreFilter 词表路径（可选，默认使用内置路径）
        """
        self.config = config
        self.store = store
        self.embedding_model = embedding_model

        # 召回层：StrategyDispatcher（始终创建，recall 阶段必须）
        # 根据 recall.mode 决定底层检索模式
        recall_mode = config.recall.mode if config.recall.mode in ("dense", "sparse", "hybrid") else "hybrid"
        self.dispatcher = StrategyDispatcher(store, embedding_model, mode=recall_mode)

        # PreFilter（规则层）
        self.prefilter: Optional[QueryPreFilter] = None
        if config.prefilter.enabled:
            self.prefilter = QueryPreFilter(kb_vocab_path=kb_vocab_path)

        # Router（LLM 层）
        self.router: Optional[QueryRouter] = None
        if config.router.enabled:
            if llm is None:
                logger.warning("router=true 但未提供 llm，回退到 router=false 模式")
                self.config.router.enabled = False
            else:
                if config.router.version == "V2":
                    from online_core.query_router_v2 import QueryRouterV2
                    self.router = QueryRouterV2(llm, fallback_llm=fallback_llm,
                                                     fallback_top_k=self.config.recall.top_k)
                    logger.info("Router 版本: V2（选择性激活 + 预算意识 Prompt）")
                else:
                    self.router = QueryRouter(llm, fallback_llm=fallback_llm)

        # Reranker
        self.reranker: Optional[CrossEncoderReranker] = None
        if config.rerank.enabled:
            try:
                self.reranker = CrossEncoderReranker(
                    model_path=config.rerank.model_path,
                    device=config.rerank.device,
                    batch_size=config.rerank.batch_size,
                )
            except FileNotFoundError as e:
                logger.warning(
                    "Reranker 模型路径不存在，回退到 recall-score 排序: %s", e
                )
                self.config.rerank.enabled = False
            except Exception as e:
                logger.warning(
                    "Reranker 加载失败 (%s: %s)，尝试 CPU fallback", type(e).__name__, e
                )
                try:
                    self.reranker = CrossEncoderReranker(
                        model_path=config.rerank.model_path,
                        device="cpu",
                        batch_size=config.rerank.batch_size,
                    )
                    logger.info("Reranker CPU fallback 成功")
                except Exception as e2:
                    logger.warning("Reranker CPU fallback 也失败 (%s)，禁用 reranker", e2)
                    self.config.rerank.enabled = False

    # ── 主入口 ────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int | None = None,
                 skip_rerank: bool = False) -> PipelineResult:
        """执行检索管线，返回统一格式的 chunk 列表。

        Args:
            query: 用户查询文本
            top_k: 最终返回数量（None 则根据 pipeline 配置决定）
            skip_rerank: 是否跳过 reranker（用于缓存 recall 结果；
                          runner 层会在缓存命中后单独执行 reranker）

        Returns:
            PipelineResult: 包含 chunk 列表和阶段耗时分解
        """
        timing: dict[str, float] = {}
        t0 = time.perf_counter()

        if top_k is None:
            # 基础截断：始终使用 recall.top_k（不依赖 rerank 配置）
            top_k = self.config.recall.top_k

        # 1. PreFilter（可选）
        t1 = time.perf_counter()
        if self.config.router.enabled and self.prefilter is not None:
            filter_result = self.prefilter.filter(query)
            if not filter_result.need_rag:
                logger.info("PreFilter 跳过 RAG: %s", filter_result.skip_reason)
                timing["prefilter"] = time.perf_counter() - t1
                timing["total"] = time.perf_counter() - t0
                return PipelineResult(chunks=[], timing=timing)
        else:
            filter_result = None
        timing["prefilter"] = time.perf_counter() - t1

        # 2. Router → RouteDecision
        t2 = time.perf_counter()
        if self.config.router.enabled and self.router is not None:
            decision = self.router.route(query, filter_result)
        else:
            # 默认模式：fallback 使用 YAML 配置的 recall.top_k 作为检索深度
            decision = RouteDecision.fallback(query, top_k=self.config.recall.top_k)
        timing["router"] = time.perf_counter() - t2

        # 收集路由信息
        route_strategies = list(set(
            ssq.strategy
            for sq in decision.subquerys
            for ssq in sq.subsubquerys
        ))
        route_difficulty = decision.difficulty
        route_detail = f"norm={decision.norm_process}, types={decision.query_type}"

        # 如果路由决定不检索
        if not decision.need_rag:
            timing["recall"] = 0.0
            timing["rerank"] = 0.0
            timing["total"] = time.perf_counter() - t0
            return PipelineResult(
                chunks=[], timing=timing,
                route_strategies=route_strategies,
                route_difficulty=route_difficulty,
                route_decision_detail=route_detail,
            )

        # 3. Recall: StrategyDispatcher
        t3 = time.perf_counter()
        response = self.dispatcher.dispatch(decision)
        candidates = self._flatten_dedup(response)
        timing["recall"] = time.perf_counter() - t3

        # 4. Rerank（条件激活：skip_rerank=True 时跳过，确保缓存不包含 reranker 输出）
        t4 = time.perf_counter()
        if (not skip_rerank
                and self.config.rerank.enabled
                and self.reranker is not None
                and len(candidates) > self.config.rerank.top_k):
            candidates = self.reranker.rerank(
                query=query,
                candidates=candidates,
                top_k=self.config.rerank.top_k,
            )
        else:
            # 无 rerank 或候选不足：按 recall 分数排序，截断
            if self.config.rerank.enabled and len(candidates) <= self.config.rerank.top_k:
                logger.debug(
                    "跳过 rerank: 候选数 %d ≤ rerank.top_k %d",
                    len(candidates), self.config.rerank.top_k,
                )
            candidates.sort(key=lambda x: x.score, reverse=True)
            candidates = candidates[:top_k]
        timing["rerank"] = time.perf_counter() - t4

        timing["total"] = time.perf_counter() - t0

        return PipelineResult(
            chunks=[RetrievedChunk.from_result(r) for r in candidates],
            timing=timing,
            route_strategies=route_strategies,
            route_difficulty=route_difficulty,
            route_decision_detail=route_detail,
        )

    # ── 工具方法 ──────────────────────────────────────────────────

    def _flatten_dedup(self, response: RetrievalResponse) -> list[RetrievalResult]:
        """展平 RetrievalResponse → 跨 SSQ 去重 + 按最高分排序 + 截断。

        对齐 engine.py 的 _flatten_results：
        1. 跨 SubQuery/SubSubQuery 合并所有 chunk
        2. 按 chunk_id 去重，保留跨 SSQ 的最高 RRF score（不是首次出现）
        3. 按 max-score 降序排序
        4. 截断到 recall.top_k（安全上限，防止多 SSQ 合并后过量）
        """
        seen: dict[str, RetrievalResult] = {}
        for sq_result in response.subquery_results:
            for ssr in sq_result.subsubresults:
                for r in ssr.chunks:
                    cid = r.chunk.chunk_id
                    if cid not in seen or r.score > seen[cid].score:
                        seen[cid] = r

        results = list(seen.values())
        results.sort(key=lambda x: x.score, reverse=True)
        budget = self.config.recall.top_k
        return results[:budget]
