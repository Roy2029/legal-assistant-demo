"""策略编排与结果收集模块。

StrategyDispatcher：
  接收 RouteDecision，根据每个 SubSubQuery 的 strategy 选择对应策略实例，
  并行执行所有 subsubquery，收集结果。

Collector：
  按 SubQuery 分组、去重、组装 RetrievalResponse。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from offline_core.retriever import (
    BaseStrategy,
    FilterStrategy,
    HierarchicalStrategy,
    HybridMethod,
    ParentChildStrategy,
    SimpleStrategy,
)
from offline_core.store import QdrantStore
from offline_core.modules import BaseEmbeddingModel
from offline_core.data_model import RetrievalResult
from online_core.data_model import (
    RetrievalResponse,
    RouteDecision,
    SubQueryResult,
    SubSubQuery,
    SubSubResult,
)

logger = logging.getLogger(__name__)

# 策略映射
STRATEGY_MAP: dict[str, type[BaseStrategy]] = {
    "simple": SimpleStrategy,
    "filter": FilterStrategy,
    "hierarchical": HierarchicalStrategy,
    "parent-child": ParentChildStrategy,
}


class StrategyDispatcher:
    """策略编排器。

    根据 RouteDecision 为每个 subsubquery 选择和执行策略，
    并行完成所有检索。
    """

    def __init__(
        self,
        store: QdrantStore,
        embedding_model: BaseEmbeddingModel,
        mode: str = "hybrid",
        max_workers: int = 4,
    ):
        self.method = HybridMethod(store, embedding_model, mode=mode)
        self.store = store
        self.mode = mode
        self.max_workers = max_workers

    def _get_strategy(self, strategy_name: str, filters: Optional[dict] = None) -> BaseStrategy:
        """根据 strategy 名称创建对应的策略实例。"""
        strategy_cls = STRATEGY_MAP.get(strategy_name)
        if strategy_cls is None:
            logger.warning(f"未知策略 '{strategy_name}'，回退到 SimpleStrategy")
            strategy_cls = SimpleStrategy

        if strategy_cls is FilterStrategy:
            if filters is None:
                logger.warning("FilterStrategy 需要 filters 参数，回退到 SimpleStrategy")
                return SimpleStrategy(self.method)
            return FilterStrategy(self.method, filters)

        if strategy_cls is HierarchicalStrategy:
            return HierarchicalStrategy(self.method, self.store)

        if strategy_cls is ParentChildStrategy:
            return ParentChildStrategy(self.method, self.store)

        return strategy_cls(self.method)

    @staticmethod
    def _apply_budget(decision: RouteDecision) -> None:
        """确保每个 SubSubQuery 有足够检索量。

        规则：
        - 每个 subsubquery 至少检索 20 条（Recall@20 为 elbow 点）
        - 如果 LLM 显式设了更大的 top_k，尊重 LLM 判断
        - 不再做硬切分 —— 最终 flat list 由 Collector/Engine 按总预算截断
        """
        MIN_PER_SSQ = 20

        all_ssq: list[SubSubQuery] = []
        for sq in decision.subquerys:
            all_ssq.extend(sq.subsubquerys)

        for ssq in all_ssq:
            if ssq.top_k < MIN_PER_SSQ:
                ssq.top_k = MIN_PER_SSQ

    def dispatch(self, decision: RouteDecision) -> RetrievalResponse:
        """执行路由决策，返回分组检索结果。

        Args:
            decision: Router 输出的路由决策

        Returns:
            RetrievalResponse: 按 SubQuery 分组的检索结果
        """
        if not decision.need_rag:
            return RetrievalResponse(
                origin_query=decision.origin_query,
                subquery_results=[],
            )

        # 预算分配：在执行检索前修正各 subsubquery 的 top_k
        self._apply_budget(decision)

        # 收集所有待执行的 subsubquery 任务
        tasks = []
        for subquery in decision.subquerys:
            for subsubquery in subquery.subsubquerys:
                tasks.append((subquery, subsubquery))

        # 并行执行
        results: list[SubSubResult] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {}
            for subquery, ssq in tasks:
                future = executor.submit(
                    self._execute_single, subquery, ssq
                )
                future_map[future] = ssq

            for future in as_completed(future_map):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    ssq = future_map[future]
                    err_msg = f"subsubquery [{ssq.subsubquery_id}] ({ssq.strategy}) 执行失败: {e}"
                    logger.error(err_msg)
                    errors.append(err_msg)
                    results.append(SubSubResult(
                        subsubquery_id=ssq.subsubquery_id,
                        subsubquery=ssq.subsubquery,
                        strategy=ssq.strategy,
                        chunks=[],
                    ))

        # 收集器：按 SubQuery 分组
        collector = Collector(decision)
        response = collector.collect(results)
        response.errors = errors
        return response

    def _execute_single(self, subquery, ssq) -> SubSubResult:
        """执行单个 subsubquery。"""
        strategy = self._get_strategy(ssq.strategy, ssq.filters)
        chunks = strategy.execute(ssq.subsubquery, top_k=ssq.top_k)
        return SubSubResult(
            subsubquery_id=ssq.subsubquery_id,
            subsubquery=ssq.subsubquery,
            strategy=ssq.strategy,
            chunks=chunks,
        )


class Collector:
    """结果收集器。

    按 SubQuery 分组，同组内按 chunk_id 去重，
    保持 SubSubResult 并列关系（不跨 subsubquery 混合排序）。
    """

    def __init__(self, decision: RouteDecision):
        self.decision = decision

    def collect(self, subsub_results: list[SubSubResult]) -> RetrievalResponse:
        """将 SubSubResult 按 SubQuery 分组并去重。

        Args:
            subsub_results: 所有 subsubquery 的执行结果

        Returns:
            RetrievalResponse: 分组后的检索响应
        """
        # 按 subquery_id 分组
        grouped: dict[int, list[SubSubResult]] = {}
        for r in subsub_results:
            # 找到这个 result 所属的 subquery
            for sq in self.decision.subquerys:
                if any(ssq.subsubquery_id == r.subsubquery_id for ssq in sq.subsubquerys):
                    grouped.setdefault(sq.subquery_id, []).append(r)
                    break

        subquery_results = []
        for sq in self.decision.subquerys:
            sq_results = grouped.get(sq.subquery_id, [])
            deduped = self._dedup_subsub_results(sq_results)
            subquery_results.append(SubQueryResult(
                subquery_id=sq.subquery_id,
                subquery=sq.subquery,
                subsubresults=deduped,
            ))

        return RetrievalResponse(
            origin_query=self.decision.origin_query,
            subquery_results=subquery_results,
        )

    @staticmethod
    def _dedup_subsub_results(subsub_results: list[SubSubResult]) -> list[SubSubResult]:
        """同一 subquery 内去重（按 chunk_id）。"""
        seen: set[str] = set()
        deduped = []
        for sr in subsub_results:
            unique_chunks = []
            for c in sr.chunks:
                if c.chunk.chunk_id not in seen:
                    seen.add(c.chunk.chunk_id)
                    unique_chunks.append(c)
            deduped.append(SubSubResult(
                subsubquery_id=sr.subsubquery_id,
                subsubquery=sr.subsubquery,
                strategy=sr.strategy,
                chunks=unique_chunks,
            ))
        return deduped
