"""通用数据模型（在线路由层）。

为三层路由架构（QueryPreFilter → Router LLM → StrategyDispatcher）
提供 RouteDecision、SubQuery、SubSubQuery 等数据模型。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubSubQuery:
    """路由分解后的最小检索单元。

    一个 SubSubQuery 对应一次独立的检索执行，
    携带 strategy 和可选 filters 让 Dispatcher 选择策略实例。
    """

    subsubquery_id: int
    subsubquery: str                  # 实际检索的文本（可能是 rewrite 后的）
    strategy: str = "simple"          # "simple" | "filter" | "hierarchical" | "parent-child"
    top_k: int = 10
    transform: str = "none"           # "none" | "hyde" | "step-back" | "decompose"
    filters: Optional[dict] = None    # strategy="filter" 时传递 Qdrant Filter


@dataclass
class SubQuery:
    """语义独立的分查询。

    一个 SubQuery 包含多个 SubSubQuery（并列检索），
    同一 SubQuery 内的多个 SubSubQuery 结果不混合排序，
    保持并列关系。
    """

    subquery_id: int
    subquery: str                      # 语义独立的分查询文本
    subsubquerys: list[SubSubQuery] = field(default_factory=list)


DIFFICULTY_VALUES = ("simple", "medium", "hard")
"""查询难度级别，对应 rerank 后保留 top_K: simple=5, medium=8, hard=10。"""


@dataclass
class RouteDecision:
    """Router LLM 的路由决策结果。

    三层结构：origin_query → subquerys → subsubquerys
    """

    origin_query: str
    need_rag: bool = True
    difficulty: str = "medium"                                # "simple" | "medium" | "hard"
    norm_issue: list[str] = field(default_factory=list)      # 识别到的规范性问题
    norm_process: list[str] = field(default_factory=list)    # 应用的处理方法
    reasoning: str = ""
    query_type: list[str] = field(default_factory=list)      # 查询类型分类
    rewrite_result: list[str] = field(default_factory=list)   # 改写后的文本
    subquerys: list[SubQuery] = field(default_factory=list)

    @staticmethod
    def fallback(query: str, top_k: int = 20) -> "RouteDecision":
        """LLM 输出异常或 Router 未启用时的默认 fallback 路由。

        Args:
            query: 查询文本
            top_k: 每个 subsubquery 的检索数量（默认 20）
        """
        return RouteDecision(
            origin_query=query,
            need_rag=True,
            difficulty="medium",
            reasoning="LLM 路由解析失败，走默认 simple strategy",
            query_type=["factoid"],
            subquerys=[
                SubQuery(
                    subquery_id=0,
                    subquery=query,
                    subsubquerys=[
                        SubSubQuery(
                            subsubquery_id=0,
                            subsubquery=query,
                            strategy="simple",
                            top_k=top_k,
                        )
                    ],
                )
            ],
        )


# ── PreFilter 输出模型 ──────────────────────────────────────


@dataclass
class FilterResult:
    """QueryPreFilter 规则层的输出。

    need_rag=False 时直接返回给上层，不再进入后续流程。
    needs_llm=True 时将初步判断作为 context 传递给 Router LLM。
    """

    origin_query: str
    need_rag: bool = True                      # 是否继续检索
    skip_reason: Optional[str] = None          # 跳过 RAG 的原因
    needs_llm: bool = True                     # 是否需要走 Router LLM
    multi_intent: bool = False
    compare: bool = False
    matched_pattern: Optional[str] = None      # 匹配到的模式名称
    suggested_strategy: Optional[str] = None   # 规则确定的 strategy
    kb_overlap: float = 0.0                    # query 与 KB 词表的重叠率


# ── 检索结果模型 ────────────────────────────────────────────

from offline_core.data_model import RetrievalResult  # noqa: E402


@dataclass
class SubSubResult:
    """单个检索单元的执行结果。"""

    subsubquery_id: int
    subsubquery: str
    strategy: str
    chunks: list[RetrievalResult] = field(default_factory=list)


@dataclass
class SubQueryResult:
    """分查询维度的检索结果（包含多个 SubSubResult）。"""

    subquery_id: int
    subquery: str
    subsubresults: list[SubSubResult] = field(default_factory=list)


@dataclass
class RetrievalResponse:
    """最终返回给上层应用的完整检索结果。"""

    origin_query: str
    subquery_results: list[SubQueryResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── Trace 数据模型 ─────────────────────────────────────────


@dataclass
class TraceConfig:
    """一次 trace 运行的检索配置。"""
    kb_name: str = ""
    index_variant: str | None = None
    retrieval_mode: str = "hybrid"
    strategy: str = "simple"
    top_k: int = 20
    rerank_top_k: int | None = None
    use_router: bool = True


@dataclass
class TraceChunk:
    """Trace 结果中的单个 chunk 条目（含展示元数据）。"""
    rank: int = 0
    chunk_id: str = ""
    doc_id: str = ""
    text: str = ""
    score: float | None = None
    rerank_score: float | None = None
    retrieval_type: str = "qdrant"
    chunk_level: str = "single"
    parent_chunk_id: str | None = None
    child_chunk_ids: list[str] = field(default_factory=list)
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    related_available: dict = field(default_factory=lambda: {
        "parent": False, "children": False, "prev": False, "next": False,
    })
    heading_path: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class SubSubTraceResult:
    """单个检索单元的 trace 记录。"""
    subsubquery_id: int = 0
    subsubquery: str = ""
    strategy: str = "simple"
    top_k: int = 10
    chunks: list[TraceChunk] = field(default_factory=list)


@dataclass
class SubTraceResult:
    """分查询维度的 trace 记录。"""
    subquery_id: int = 0
    subquery: str = ""
    subsubresults: list[SubSubTraceResult] = field(default_factory=list)


@dataclass
class TraceResult:
    """完整的检索 trace 结果（不含 LLM 生成）。"""
    trace_id: str = ""
    created_at: str = ""
    query: str = ""
    config: TraceConfig = field(default_factory=TraceConfig)
    prefilter: dict = field(default_factory=dict)
    router: dict = field(default_factory=dict)
    router_skipped: bool = False
    recall: dict = field(default_factory=lambda: {
        "subquery_results": [],
        "flat_candidates": [],
    })
    rerank: dict = field(default_factory=lambda: {
        "enabled": True,
        "before": [],
        "after": [],
    })
    qa: dict | None = None
    bm25_tokens: list[dict] | None = None  # BM25 分词诊断：token/idf/is_oov/bm25_score/freq
    planner_estimator: dict | None = None  # {"activated": bool, "strategy": str, "prob_a": float, "prob_b": float}
    timing: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
