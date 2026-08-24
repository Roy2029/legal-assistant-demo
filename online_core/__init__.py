"""Online Core — 在线路由与检索组件。

三层闸门架构：
  LegalPreFilter (法律规则层) → PlannerEstimator (ML 激活判定) → PlannerLLM (LLM 层)

在线流程编排（OnlineEngine）：
  PreFilter → Estimator → PlannerLLM → StrategyDispatcher → Reranker → Context → LLM
"""

from online_core.data_model import (
    DIFFICULTY_VALUES,
    FilterResult,
    RetrievalResponse,
    RouteDecision,
    SubQuery,
    SubQueryResult,
    SubSubQuery,
    SubSubResult,
)
from online_core.engine import OnlineEngine
from online_core.legal_pre_filter import LegalPreFilter
from online_core.planner_estimator import PlannerEstimator
from online_core.query_router import QueryPreFilter
from online_core.query_router_v2 import QueryRouterV2
from online_core.reranker import CrossEncoderReranker
from online_core.strategy_dispatcher import Collector, StrategyDispatcher
from online_core.trace_store import PipelineTrace, StageRecord, TraceStore

__all__ = [
    # 引擎
    "OnlineEngine",
    # 闸门架构
    "LegalPreFilter",
    "PlannerEstimator",
    "QueryRouterV2",
    # 旧版路由组件（已废弃，保留兼容）
    "QueryPreFilter",
    # 策略编排
    "StrategyDispatcher",
    "Collector",
    # 重排组件
    "CrossEncoderReranker",
    # 追踪
    "PipelineTrace",
    "StageRecord",
    "TraceStore",
    # 数据模型
    "DIFFICULTY_VALUES",
    "FilterResult",
    "RouteDecision",
    "SubQuery",
    "SubSubQuery",
    "SubSubResult",
    "SubQueryResult",
    "RetrievalResponse",
]
