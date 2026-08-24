"""OnlineEngine — 在线全流程编排器。

职责：
  LegalPreFilter → PlannerEstimator → PlannerLLM → retriever → reranker → LLM

完整链路：
  1. LegalPreFilter: 法律领域预过滤器（规则层短路）
  2. PlannerEstimator: ML 驱动 PlannerLLM 激活判定（短路或走 LLM 路由）
  3. PlannerLLM: LLM 驱动的查询路由（策略/改写/分解）
  4. StrategyDispatcher → Hybrid 检索 → CrossEncoderReranker → LLM

替代 ChatEngine（已废弃），使用 online_core 新架构组件。
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from offline_core.data_model import RetrievalResult
from offline_core.embedder import HuggingFaceEmbeddingModel
from offline_core.incremental_indexer import check_and_rebuild_bm25
from offline_core.modules import BaseEmbeddingModel
from offline_core.store import QdrantConfig, QdrantStore
from online_core.context_manager import ContextManager
from online_core.data_model import (
    DIFFICULTY_VALUES,
    RetrievalResponse,
    RouteDecision,
    TraceChunk,
    TraceConfig,
    TraceResult,
    SubTraceResult,
    SubSubTraceResult,
)
from online_core.legal_pre_filter import LegalPreFilter
from online_core.planner_estimator import PlannerEstimator
from online_core.query_router import QueryRouter
from online_core.query_router_v2 import QueryRouterV2 as PlannerLLM
from online_core.reranker import CrossEncoderReranker
from online_core.session_manager import SessionManager
from online_core.strategy_dispatcher import StrategyDispatcher
from online_core.trace_store import PipelineTrace, StageRecord, TraceStore
from utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

# difficulty → rerank top_k 映射
DIFFICULTY_TOP_K: dict[str, int] = {
    "simple": 5,
    "medium": 8,
    "hard": 10,
}


class OnlineEngine:
    """RAG 在线全流程编排器。

    完整链路：
      LegalPreFilter (法律规则短路) → PlannerEstimator (ML 激活判定)
      → PlannerLLM (LLM 路由) → StrategyDispatcher (检索)
      → CrossEncoderReranker (精排) → ContextManager (上下文组装) → LLM 生成

    三种分支路径：
      1. legal_pre_filter 拦截 → 直接回复（无RAG）
      2. planner_estimator 不激活 → 默认检索（simple, top_k=30）+ reranker → LLM
      3. planner_estimator 激活 → PlannerLLM 全量路由 → 检索 + reranker → LLM

    用法：
        engine = OnlineEngine(config, llm)
        engine.load_index("laws")
        result = engine.process("什么是违约责任？", history=[...])
        print(result["response"])
    """

    def __init__(
        self,
        config,
        llm,
        local_model_path: Optional[str] = None,
        device: str = "cuda",
        cost_tracker: Optional[CostTracker] = None,
    ):
        self.config = config
        self.llm = llm
        self.device = device

        # Token 计费追踪
        self.cost_tracker = cost_tracker or CostTracker()

        # 如果 LLM 支持 cost_tracker 注入，传递给它
        if hasattr(self.llm, 'cost_tracker') and self.llm.cost_tracker is None:
            self.llm.cost_tracker = self.cost_tracker

        # 本地模型路径（embedding + reranker 共用基础路径）
        self.local_model_path = local_model_path or (
            "./local_model" if Path("./local_model").exists() else None
        )

        # 检索设置（可从 config 覆盖）
        self.top_k = config.get("retriever.top_k", 5)
        self.mode = config.get("retriever.mode", "hybrid")

        # ── 初始化固定组件 ──

        # 1. LegalPreFilter（法律领域预过滤）
        prefilter_cfg = config.get("prefilter.legal_filter", {})
        self.pre_filter = LegalPreFilter(
            kb_vocab_path=prefilter_cfg.get("kb_vocab_path", "index_store/kb_vocab.json"),
            legal_dict_path=prefilter_cfg.get("legal_dict_path", "experiments/data/legal_dict.txt"),
            kw_weight=prefilter_cfg.get("kw_weight", 0.70),
            total_thresh=prefilter_cfg.get("total_thresh", 0.20),
        )
        if config.get("logging.detailed", False):
            self.pre_filter.set_detailed_logs(True)

        # 2. PlannerEstimator（ML 驱动的 PlannerLLM 激活判定）
        estimator_cfg = config.get("planner_estimator", {})
        self.planner_estimator = None
        if estimator_cfg.get("enabled", True):
            try:
                self.planner_estimator = PlannerEstimator(
                    model_dir=estimator_cfg.get(
                        "model_dir", "experiments/planner-utility-estimator/models/"
                    ),
                    strategy=estimator_cfg.get("strategy", "A∪B"),
                    activation_threshold=estimator_cfg.get("activation_threshold", 0.5),
                )
            except Exception as e:
                logger.warning("PlannerEstimator 初始化失败，降级为 always-activate: %s", e)
                self.planner_estimator = None

        # 3. PlannerLLM（LLM 路由，激活判定由 Estimator 管理）
        self.planner_llm = PlannerLLM(llm)

        # 4. 其他组件
        self.context_manager = ContextManager(config, llm)
        self.session_manager = SessionManager()
        self.trace_store = TraceStore()

        # ── 重排器（懒加载，需要模型文件） ──
        rerank_cfg = config.get("rerank", {})
        self.rerank_enabled = rerank_cfg.get("enabled", True)
        self._reranker: Optional[CrossEncoderReranker] = None
        if self.rerank_enabled:
            try:
                self._reranker = CrossEncoderReranker(
                    model_path=rerank_cfg.get(
                        "model", "local_model/bge-reranker-v2-m3"
                    ),
                    device=rerank_cfg.get("device", self.device),
                )
            except FileNotFoundError as e:
                logger.warning("Reranker 初始化失败（模型未下载）: %s", e)
                logger.warning("Rerank 已禁用，将使用检索分排序。")
                self.rerank_enabled = False
            except Exception as e:
                logger.warning("Reranker 初始化异常: %s", e)
                self.rerank_enabled = False

        # ── 运行时状态（按需创建） ──
        self._store: Optional[QdrantStore] = None
        self._embedding_model: Optional[BaseEmbeddingModel] = None
        self._dispatcher: Optional[StrategyDispatcher] = None
        self._db_name: Optional[str] = None

    # ── 属性 ────────────────────────────────────────────────────────

    @property
    def active_db(self) -> Optional[str]:
        return self._db_name

    @property
    def is_ready(self) -> bool:
        return self._dispatcher is not None

    # ── 知识库管理 ─────────────────────────────────────────────────

    def list_databases(self) -> list[str]:
        """发现可用知识库。

        支持两种布局：
        1. Qdrant 嵌入式数据库路径下已有 collection → 用 collection 名作为 KB 名
        2. 旧式 index_store_dir/<db_name>/faiss/... → 传统目录扫描

        Returns:
            知识库名称列表
        """
        dbs: list[str] = []

        # 1. Qdrant collection 发现
        qdrant_path = self._get_qdrant_path()
        if qdrant_path and (qdrant_path / "meta.json").exists():
            import json
            try:
                meta = json.loads((qdrant_path / "meta.json").read_text())
                dbs.extend(meta.get("collections", {}).keys())
            except Exception:
                pass

        # 2. Per-KB Qdrant 布局检测（data/indices/<db>/qdrant/meta.json）
        #    优先使用 config 中的 index_store_dir，若不存在则 fallback 到 data/indices
        index_store = Path(
            self.config.get("index_store_dir", "data/indices")
        )
        if not index_store.exists():
            index_store = Path("data/indices")
        if index_store.exists():
            for d in sorted(index_store.iterdir()):
                if d.is_dir() and (d / "qdrant" / "meta.json").exists():
                    name = d.name
                    if name not in dbs:
                        dbs.append(name)

        # 3. 传统 FAISS/BM25 目录扫描（过渡期兼容）
        if index_store.exists():
            for d in sorted(index_store.iterdir()):
                if d.is_dir():
                    has_faiss = (d / "faiss" / "faiss.index").exists()
                    has_bm25 = (d / "bm25" / "bm25_data.pkl").exists()
                    if has_faiss or has_bm25:
                        name = d.name
                        if name not in dbs:
                            dbs.append(name)

        return sorted(dbs)

    def _get_qdrant_path(self) -> Optional[Path]:
        """获取 Qdrant 嵌入式数据库根路径。"""
        index_store = Path(
            self.config.get("index_store_dir", "data/indices")
        )
        qdrant_path = index_store / "qdrant"
        if qdrant_path.exists():
            return qdrant_path
        # 如果 config 路径不存在，fallback 到 data/indices
        fallback = Path("data/indices") / "qdrant"
        if fallback.exists():
            return fallback
        # 兼容旧版配置
        vdb_path = Path(
            self.config.get("vector_db.path", "./index_store")
        )
        if vdb_path.exists():
            return vdb_path
        return None

    def _get_qdrant_path_for_db(self, db_name: str) -> Optional[Path]:
        """获取指定知识库的 Qdrant 存储路径。

        优先查找 per-KB 布局：<index_store>/{db_name}/qdrant/
        回退到旧式扁平布局：<index_store>/qdrant/
        """
        index_store = Path(
            self.config.get("index_store_dir", "data/indices")
        )
        per_kb = index_store / db_name / "qdrant"
        if per_kb.exists():
            return per_kb
        # 如果 config 路径不存在，fallback 到 data/indices
        fallback_kb = Path("data/indices") / db_name / "qdrant"
        if fallback_kb.exists():
            return fallback_kb
        return self._get_qdrant_path()

    def _read_manifest_model(self, db_name: str) -> tuple[Optional[str], Optional[int]]:
        """从 manifest.json 读取嵌入模型元数据。

        Args:
            db_name: 知识库名称

        Returns:
            (model_name, dimension) 或 (None, None) 如果 manifest 不存在/无模型字段
        """
        index_store = Path(
            self.config.get("index_store_dir", "data/indices")
        )
        manifest_path = index_store / db_name / "manifest.json"
        if not manifest_path.exists():
            return None, None

        try:
            from offline_core.manifest import Manifest
            manifest = Manifest.load(manifest_path)
            model = manifest.embedding_model or None
            dim = manifest.embedding_dimension if manifest.embedding_dimension > 0 else None
            if model:
                logger.info("从 manifest 检测到嵌入模型: %s (dim=%s)", model, dim or "未知")
            return model, dim
        except Exception as e:
            logger.warning("读取 manifest 失败: %s，fallback 到 config", e)
            return None, None

    def load_index(self, db_name: str) -> bool:
        """加载指定知识库。

        流程：
        0. 从 manifest.json 读取模型元数据（优先级最高）
        1. 加载 embedding 模型
        2. 创建 QdrantStore（指向嵌入式数据库路径下的指定 collection）
        3. Qdrant 维度交叉校验
        4. 构建 StrategyDispatcher

        Args:
            db_name: 知识库名称（对应 Qdrant collection 名或 FAISS 目录名）

        Returns:
            是否加载成功
        """
        # 0. 尝试从 manifest 读取模型元数据
        manifest_model, manifest_dim = self._read_manifest_model(db_name)

        # 1. 加载 embedding 模型
        try:
            # 优先级：manifest > local_model_path > config > 硬编码默认值
            model_name = manifest_model or self.local_model_path or self.config.get(
                "vector_db.dense.embedding_model", "local_model/bge-base-zh"
            )
            dim = manifest_dim or self.config.get("vector_db.dense.dimension", 768)
            logger.info(
                "加载 embedding 模型: %s (device=%s, dim=%d)",
                model_name, self.device, dim,
            )
            self._embedding_model = HuggingFaceEmbeddingModel(
                model_name=model_name,
                device=self.device,
            )
        except Exception as e:
            logger.error("Embedding 模型加载失败: %s", e)
            return False

        # 2. 尝试 Qdrant 路径（per-KB 布局优先）
        qdrant_path = self._get_qdrant_path_for_db(db_name)
        if qdrant_path:
            try:
                # 从 meta.json 读取实际的 collection 名称（可能与 db_name 不同）
                collection_name = db_name
                meta_path = qdrant_path / "meta.json"
                if meta_path.exists():
                    try:
                        import json as _json
                        meta = _json.loads(meta_path.read_text())
                        collections = meta.get("collections", {})
                        if collections:
                            collection_name = next(iter(collections.keys()), db_name)
                    except Exception:
                        pass

                qdrant_config = QdrantConfig(
                    mode="embedded",
                    path=str(qdrant_path),
                    collection_name=collection_name,
                    dense_dimension=dim,
                    dense_distance=self.config.get(
                        "vector_db.dense.distance", "Cosine"
                    ),
                    enable_sparse=self.config.get(
                        "vector_db.sparse.enabled", True
                    ),
                )
                store = QdrantStore(qdrant_config)
                # 验证 collection 存在
                store.client.get_collection(collection_name)

                # Qdrant 维度交叉校验
                try:
                    collection_info = store.client.get_collection(collection_name)
                    actual_dim = collection_info.config.params.vectors["dense"].size
                    if actual_dim != dim:
                        logger.warning(
                            "维度不匹配: 预期 %d，Qdrant 实际 %d。使用实际维度。",
                            dim, actual_dim,
                        )
                        dim = actual_dim
                except Exception as e:
                    logger.debug("Qdrant 维度校验跳过: %s", e)

                # 加载已缓存的 BM25 encoder（如果存在）
                bm25_path = qdrant_path / "bm25_encoder.pkl"
                if bm25_path.exists() and qdrant_config.enable_sparse:
                    try:
                        from offline_core.store import BM25Encoder
                        store.set_bm25_encoder(BM25Encoder.load(str(bm25_path)))
                        logger.info("BM25 encoder 已从 %s 加载", bm25_path)
                    except Exception as e:
                        logger.warning("BM25 encoder 加载失败 (%s): %s", bm25_path, e)

                self._store = store
                self._dispatcher = StrategyDispatcher(
                    store, self._embedding_model
                )
                self._db_name = db_name

                # BM25 延迟重算（检查 dirty 标记）
                check_and_rebuild_bm25(store, db_name, self.config)

                logger.info("知识库 [%s] 已加载（Qdrant）", db_name)
                return True
            except Exception as e:
                logger.warning(
                    "Qdrant 加载失败 (collection=%s): %s，尝试旧索引...",
                    db_name, e,
                )

        # 3. Qdrant 不可用时回退旧索引（过渡期）
        return self._load_legacy_index(db_name)

    def _load_legacy_index(self, db_name: str) -> bool:
        """加载旧式 FAISS/BM25 索引（过渡兼容）。"""
        index_store = Path(
            self.config.get("index_store_dir", "data/indices")
        )
        db_dir = index_store / db_name
        if not db_dir.exists():
            # fallback 到 data/indices
            db_dir = Path("data/indices") / db_name
        if not db_dir.exists():
            logger.warning("知识库目录不存在: %s", db_dir)
            return False

        faiss_dir = db_dir / "faiss"
        bm25_dir = db_dir / "bm25"

        has_faiss = faiss_dir.exists()
        has_bm25 = bm25_dir.exists()

        if not has_faiss and not has_bm25:
            logger.warning("知识库 %s 无有效索引", db_name)
            return False

        # 对于旧架构，直接构建 retriever 而非 dispatcher
        # 这里使用兼容模式——OnlineEngine 仍然可以处理旧索引
        # 但路由/重排全部基于在线架构，仅检索层使用旧索引
        logger.warning(
            "知识库 [%s] 加载为旧索引模式（FAISS/BM25）。"
            "建议迁移至 Qdrant 以获得完整功能。",
            db_name,
        )

        from offline_core.retriever import (
            BM25Retriever,
            DenseRetriever,
            HybridRetriever,
            RRFFusion,
        )
        from offline_core.store import BM25Store, FAISSStore

        retrievers = []
        if has_faiss:
            faiss_store = FAISSStore(
                dimension=self._embedding_model.dimension
            )
            faiss_store.load(str(faiss_dir))
            retrievers.append(
                DenseRetriever(faiss_store, self._embedding_model)
            )
        if has_bm25:
            bm25_store = BM25Store()
            bm25_store.load(str(bm25_dir))
            retrievers.append(BM25Retriever(bm25_store))

        # 包装为 SimpleStrategy 兼容接口
        hybrid = HybridRetriever(retrievers, RRFFusion(k=60))

        # 将旧的 retriever 接口包装为 dispatch 接口
        self._legacy_retriever = hybrid
        self._db_name = db_name
        logger.info("知识库 [%s] 已加载（旧索引兼容模式）", db_name)
        return True

    # ── 设置更新 ───────────────────────────────────────────────────

    def update_settings(self, **kwargs) -> None:
        """更新运行设置。"""
        for key in ("mode", "top_k", "device", "local_model_path"):
            if key in kwargs:
                setattr(self, key, kwargs[key])

        if self._db_name:
            # 设置变化后重新加载 KB
            self.load_index(self._db_name)

    # ── 核心流程 ───────────────────────────────────────────────────

    def trace(
        self,
        query: str,
        *,
        kb_name: str | None = None,
        index_variant: str | None = None,
        retrieval_mode: str = "hybrid",
        strategy_override: str | None = None,
        top_k: int = 20,
        rerank_top_k: int | None = None,
        use_router: bool = True,
        persist_trace: bool = True,
    ) -> "TraceResult":
        """运行检索 trace，不生成 LLM 回复。

        Args:
            query: 查询文本
            kb_name: 知识库名称
            index_variant: 索引变体名称
            retrieval_mode: 检索模式（dense/sparse/hybrid）
            strategy_override: 策略覆盖（simple/filter/hierarchical/parent-child）
            top_k: 检索 top_k
            rerank_top_k: rerank top_k
            use_router: 是否启用 PlannerLLM（如 Estimator 激活）
            persist_trace: 是否持久化

        Returns:
            TraceResult: 完整 trace 结果
        """
        from datetime import datetime, timezone
        import uuid

        fallback_top_k = self.config.get("planner_llm.fallback_top_k", 30)

        trace_result = TraceResult(
            trace_id=f"trace_{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            query=query,
            config=TraceConfig(
                kb_name=kb_name or self._db_name or "",
                index_variant=index_variant,
                retrieval_mode=retrieval_mode,
                strategy=strategy_override or "simple",
                top_k=top_k,
                rerank_top_k=rerank_top_k,
                use_router=use_router,
            ),
            timing={},
            cost={},
            errors=[],
        )
        pipeline_trace = PipelineTrace(
            trace_id=trace_result.trace_id.replace("trace_", "trc_"),
            query=query,
        )

        # ── 1. LegalPreFilter ──
        t0 = _now_ms()
        try:
            filter_result = self.pre_filter.filter(query)
            trace_result.prefilter = {
                "need_rag": filter_result.need_rag,
                "skip_reason": filter_result.skip_reason,
                "matched_pattern": filter_result.matched_pattern,
                "suggested_strategy": filter_result.suggested_strategy,
                "kb_overlap": filter_result.kb_overlap,
            }
            pipeline_trace.stages.append(StageRecord(
                stage_name="legal_pre_filter",
                status="BLOCKED" if not filter_result.need_rag else "PASSED",
                detail={"skip_reason": filter_result.skip_reason},
                timing_ms=_elapsed(t0),
            ))
            if not filter_result.need_rag:
                trace_result.timing["prefilter_ms"] = _elapsed(t0)
                trace_result.timing["total_ms"] = _elapsed(t0)
                pipeline_trace.total_timing_ms = _elapsed(t0)
                self.trace_store.record(pipeline_trace)
                return trace_result
        except Exception as e:
            trace_result.errors.append(f"prefilter 异常: {e}")
            trace_result.timing["prefilter_ms"] = _elapsed(t0)

        trace_result.timing["prefilter_ms"] = _elapsed(t0)

        # ── 2. PlannerEstimator → PlannerLLM / Fallback ──
        t1 = _now_ms()
        decision = None
        activated = False
        if use_router and self.planner_estimator:
            try:
                activated = self.planner_estimator.should_activate(query)
                proba = self.planner_estimator.predict_proba(query)
                pipeline_trace.stages.append(StageRecord(
                    stage_name="planner_estimator",
                    status="ACTIVATED" if activated else "NOT_ACTIVATED",
                    detail=proba,
                    timing_ms=_elapsed(t1),
                ))
                trace_result.planner_estimator = {
                    "activated": activated,
                    "strategy": self.planner_estimator.strategy,
                    "prob_a": proba.get("prob_a", 0.0) if isinstance(proba, dict) else 0.0,
                    "prob_b": proba.get("prob_b", 0.0) if isinstance(proba, dict) else 0.0,
                }
            except Exception as e:
                trace_result.errors.append(f"planner_estimator 异常: {e}")
                activated = True  # 异常时保守激活
                trace_result.planner_estimator = {
                    "activated": True,
                    "strategy": "error_fallback",
                    "prob_a": 0.0,
                    "prob_b": 0.0,
                    "error": str(e),
                }
        else:
            activated = use_router  # use_router=True → 激活（走全量路由）；否则 fallback
            pipeline_trace.stages.append(StageRecord(
                stage_name="planner_estimator",
                status="SKIPPED",
                detail={"reason": "use_router=" + str(use_router)},
                timing_ms=_elapsed(t1),
            ))
            trace_result.planner_estimator = {
                "activated": activated,
                "strategy": "bypass",
                "prob_a": 0.0,
                "prob_b": 0.0,
            }

        t_route = _now_ms()
        if activated:
            try:
                decision = self.planner_llm.route(query, filter_result)
                trace_result.router = {
                    "need_rag": decision.need_rag,
                    "difficulty": decision.difficulty,
                    "query_type": decision.query_type,
                    "reasoning": decision.reasoning,
                    "norm_issue": decision.norm_issue,
                    "norm_process": decision.norm_process,
                    "rewrite_result": decision.rewrite_result,
                    "subquerys": [
                        {
                            "subquery_id": sq.subquery_id,
                            "subquery": sq.subquery,
                            "subsubquerys": [
                                {
                                    "subsubquery_id": ssq.subsubquery_id,
                                    "subsubquery": ssq.subsubquery,
                                    "strategy": ssq.strategy,
                                    "top_k": ssq.top_k,
                                    "transform": ssq.transform,
                                }
                                for ssq in sq.subsubquerys
                            ],
                        }
                        for sq in decision.subquerys
                    ],
                }
                trace_result.router_skipped = False
                pipeline_trace.stages.append(StageRecord(
                    stage_name="planner_llm",
                    status="PASSED",
                    detail={
                        "difficulty": decision.difficulty,
                        "query_type": decision.query_type,
                        "n_subquerys": len(decision.subquerys),
                    },
                    timing_ms=_elapsed(t_route),
                ))
            except Exception as e:
                trace_result.errors.append(f"planner_llm 异常: {e}, 使用 fallback")
                decision = RouteDecision.fallback(query, top_k=fallback_top_k)
                trace_result.router = {"need_rag": True, "fallback": True}
                trace_result.router_skipped = False
                pipeline_trace.stages.append(StageRecord(
                    stage_name="planner_llm",
                    status="FALLBACK",
                    detail={"error": str(e)},
                    timing_ms=_elapsed(t_route),
                ))
        else:
            trace_result.router_skipped = True
            decision = RouteDecision.fallback(query, top_k=fallback_top_k)
            trace_result.router = {"need_rag": True, "fallback": True, "skipped": True}
            pipeline_trace.stages.append(StageRecord(
                stage_name="planner_llm",
                status="SKIPPED",
                detail={"reason": "estimator_not_activated", "fallback_top_k": fallback_top_k},
                timing_ms=_elapsed(t_route),
            ))

        trace_result.timing["router_ms"] = _elapsed(t1)

        # ── 2.5 Strategy Override ──
        if strategy_override and decision:
            for sq in decision.subquerys:
                for ssq in sq.subsubquerys:
                    ssq.strategy = strategy_override

        # ── 3. Dispatch Recall ──
        t2 = _now_ms()
        all_chunks: list[RetrievalResult] = []
        subquery_results = []
        dispatch_errors = []

        if self._dispatcher and decision:
            try:
                # 根据 retrieval_mode 临时调整 dispatcher 的 mode
                original_mode = self._dispatcher.mode
                if retrieval_mode != original_mode:
                    from offline_core.retriever import HybridMethod
                    self._dispatcher.method = HybridMethod(
                        self._dispatcher.store,
                        self._embedding_model,
                        mode=retrieval_mode,
                    )
                    self._dispatcher.mode = retrieval_mode

                # 结构化日志：dispatch 前的元信息
                n_subqueries = len(decision.subquerys)
                n_subsubqueries = sum(len(sq.subsubquerys) for sq in decision.subquerys)
                logger.info(
                    "Retrieval dispatch: mode=%s, subqueries=%d, subsubqueries=%d, top_k=%d",
                    retrieval_mode, n_subqueries, n_subsubqueries, top_k,
                )

                response: RetrievalResponse = self._dispatcher.dispatch(decision)

                # 收集 dispatcher 中 subsubquery 级的异常到 trace_result.errors
                if response.errors:
                    trace_result.errors.extend(response.errors)
                    dispatch_errors = response.errors

                # 结构化日志：dispatch 结果
                recall_ms = _elapsed(t2)
                logger.info(
                    "Retrieval dispatch done: mode=%s, subqueries=%d, results=%d, errors=%d, time=%.0fms",
                    retrieval_mode, n_subqueries,
                    sum(len(sq.subsubresults) for sq in response.subquery_results),
                    len(dispatch_errors), recall_ms,
                )

                # 恢复 mode
                if retrieval_mode != original_mode:
                    self._dispatcher.method = HybridMethod(
                        self._dispatcher.store,
                        self._embedding_model,
                        mode=original_mode,
                    )
                    self._dispatcher.mode = original_mode

                # 构建分组结果
                for sq_result in response.subquery_results:
                    sub_trace = SubTraceResult(
                        subquery_id=sq_result.subquery_id,
                        subquery=sq_result.subquery,
                    )
                    for ss_result in sq_result.subsubresults:
                        ss_trace = SubSubTraceResult(
                            subsubquery_id=ss_result.subsubquery_id,
                            subsubquery=ss_result.subsubquery,
                            strategy=ss_result.strategy,
                            top_k=top_k,
                        )
                        for i, c in enumerate(ss_result.chunks):
                            tc = _retrieval_result_to_trace_chunk(c, i + 1)
                            ss_trace.chunks.append(tc)
                        sub_trace.subsubresults.append(ss_trace)
                    subquery_results.append(sub_trace)

                # 展平去重
                all_chunks = self._flatten_results(response)

            except Exception as e:
                trace_result.errors.append(f"recall 异常: {e}")
        elif hasattr(self, "_legacy_retriever") and decision:
            try:
                all_chunks = self._legacy_retrieve(query, decision)
                # 旧索引无分组信息
                sq_results = []
                for sq in decision.subquerys:
                    sqr = SubTraceResult(
                        subquery_id=sq.subquery_id,
                        subquery=sq.subquery,
                    )
                    for ssq in sq.subsubquerys:
                        sst = SubSubTraceResult(
                            subsubquery_id=ssq.subsubquery_id,
                            subsubquery=ssq.subsubquery,
                            strategy=ssq.strategy,
                            top_k=ssq.top_k,
                        )
                        sqr.subsubresults.append(sst)
                    sq_results.append(sqr)
                subquery_results = sq_results
            except Exception as e:
                trace_result.errors.append(f"legacy recall 异常: {e}")

        trace_result.timing["recall_ms"] = _elapsed(t2)
        pipeline_trace.stages.append(StageRecord(
            stage_name="retrieval",
            status="PASSED",
            detail={
                "mode": retrieval_mode,
                "n_results": len(all_chunks),
                "n_subqueries": len(subquery_results),
            },
            timing_ms=_elapsed(t2),
        ))

        # ── 4. Flatten candidates ──
        flat_candidates = []
        seen_ids = set()
        for i, c in enumerate(all_chunks):
            cid = c.chunk.chunk_id
            if cid not in seen_ids:
                seen_ids.add(cid)
                tc = _retrieval_result_to_trace_chunk(c, i + 1)
                flat_candidates.append(tc)

        trace_result.recall = {
            "subquery_results": [
                _sub_trace_to_dict(st) for st in subquery_results
            ],
            "flat_candidates": [fc.__dict__ for fc in flat_candidates],
        }

        # ── 4.5 BM25 分词诊断（sparse/hybrid 模式） ──
        if retrieval_mode in ("sparse", "hybrid") and self._dispatcher is not None:
            try:
                bm25_enc = self._dispatcher.store.bm25_encoder
                if bm25_enc is not None:
                    tokens_info = bm25_enc.tokenize_with_weights(query)
                    trace_result.bm25_tokens = tokens_info

                    # detailed_logs 模式输出更多诊断信息
                    if self.config.get("logging.detailed", False) and tokens_info:
                        oov_tokens = [t["token"] for t in tokens_info if t["is_oov"]]
                        in_vocab = [t for t in tokens_info if not t["is_oov"]]
                        logger.info(
                            "BM25诊断 | query=\"%s\" | tokens=%d, OOV=%d%s, mean_idf=%.3f",
                            query[:60],
                            len(tokens_info),
                            len(oov_tokens),
                            f" ({', '.join(oov_tokens[:10])})" if oov_tokens else "",
                            sum(t["idf"] for t in in_vocab) / len(in_vocab) if in_vocab else 0,
                        )

                        # top-K 共享 token 统计
                        if all_chunks:
                            t_top_k = min(5, len(all_chunks))
                            query_token_set = {t["token"] for t in tokens_info if not t["is_oov"]}
                            logger.info("BM25诊断 | query_tokens=%s", sorted(query_token_set))
                            for i in range(t_top_k):
                                chunk_text = all_chunks[i].chunk.text
                                chunk_tokens = bm25_enc.tokenize(chunk_text)
                                chunk_set = set(chunk_tokens)
                                shared = query_token_set & chunk_set
                                logger.info(
                                    "BM25诊断 |   rank=#%d score=%.4f shared=%d/%d tokens=%s",
                                    i + 1, all_chunks[i].score,
                                    len(shared), len(query_token_set),
                                    sorted(shared) if shared else "-",
                                )
            except Exception as e:
                logger.debug("BM25 分词诊断获取失败: %s", e)

        # ── 5. Rerank ──
        t3 = _now_ms()
        rerank_before = list(flat_candidates)  # copy
        rerank_after = []
        rerank_enabled = self.rerank_enabled and self._reranker is not None and len(flat_candidates) > 0

        if rerank_enabled:
            try:
                actual_top_k = rerank_top_k if rerank_top_k is not None else top_k
                reranked = self._reranker.rerank(query, all_chunks, actual_top_k)
                rerank_after = []
                for i, c in enumerate(reranked):
                    tc = _retrieval_result_to_trace_chunk(c, i + 1)
                    tc.rerank_score = c.score if hasattr(c, 'score') else None
                    rerank_after.append(tc)
            except Exception as e:
                trace_result.errors.append(f"rerank 异常: {e}")
                rerank_enabled = False

        trace_result.rerank = {
            "enabled": rerank_enabled,
            "before": [rb.__dict__ for rb in rerank_before],
            "after": [ra.__dict__ for ra in rerank_after] if rerank_after else [],
        }

        trace_result.timing["rerank_ms"] = _elapsed(t3)
        pipeline_trace.stages.append(StageRecord(
            stage_name="reranker",
            status="PASSED" if rerank_enabled else "SKIPPED",
            detail={
                "enabled": rerank_enabled,
                "rerank_top_k": actual_top_k if rerank_enabled else None,
            },
            timing_ms=_elapsed(t3),
        ))

        # ── 汇总 ──
        total = sum(trace_result.timing.values())
        trace_result.timing["total_ms"] = round(total, 2)

        # Cost
        trace_result.cost = self.cost_tracker.session_summary()

        pipeline_trace.total_timing_ms = round(total, 2)
        if persist_trace:
            self.trace_store.record(pipeline_trace)

        return trace_result

    def process(
        self,
        user_input: str,
        history: Optional[list[dict]] = None,
    ) -> dict:
        """完整处理一次用户输入。

        Args:
            user_input: 用户输入文本
            history: 历史消息列表 [{"role": ..., "content": ...}, ...]

        Returns:
            {
                "response": str,           # LLM 回答
                "route": dict,             # 路由决策摘要
                "retrieval_context": str | None,  # 格式化后的上下文
                "rerank_scores": list | None,     # CE 精排分数
                "trace_id": str,           # 全链路追踪 ID
            }
        """
        trace = PipelineTrace(
            trace_id=f"trc_{uuid.uuid4().hex[:12]}",
            query=user_input,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        fallback_top_k = self.config.get("planner_llm.fallback_top_k", 30)

        # ── 1. LegalPreFilter（规则层快速短路） ──
        t0 = _now_ms()
        filter_result = self.pre_filter.filter(user_input)
        trace.stages.append(StageRecord(
            stage_name="legal_pre_filter",
            status="BLOCKED" if not filter_result.need_rag else "PASSED",
            detail={
                "skip_reason": filter_result.skip_reason,
                "kw_score": filter_result.kb_overlap,
            },
            timing_ms=_elapsed(t0),
        ))
        if not filter_result.need_rag:
            response_text = self._direct_reply(filter_result.skip_reason)
            trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
            self.trace_store.record(trace)
            return {
                "response": response_text,
                "route": {
                    "need_rag": False,
                    "skip_reason": filter_result.skip_reason,
                },
                "retrieval_context": None,
                "rerank_scores": None,
                "cost": self.cost_tracker.session_summary(),
                "trace_id": trace.trace_id,
            }

        # ── 2. PlannerEstimator → 决定是否走 PlannerLLM ──
        t0 = _now_ms()
        activated = False
        if self.planner_estimator:
            activated = self.planner_estimator.should_activate(user_input)
            trace.stages.append(StageRecord(
                stage_name="planner_estimator",
                status="ACTIVATED" if activated else "NOT_ACTIVATED",
                detail=self.planner_estimator.predict_proba(user_input),
                timing_ms=_elapsed(t0),
            ))
        else:
            # Estimator 未加载，降级为激活（走 PlannerLLM）
            activated = True
            trace.stages.append(StageRecord(
                stage_name="planner_estimator",
                status="SKIPPED",
                detail={"reason": "estimator_not_loaded"},
                timing_ms=_elapsed(t0),
            ))

        # ── 3. PlannerLLM / Fallback ──
        t0 = _now_ms()
        if activated:
            decision = self.planner_llm.route(user_input, filter_result)
            trace.stages.append(StageRecord(
                stage_name="planner_llm",
                status="PASSED",
                detail={
                    "difficulty": decision.difficulty,
                    "query_type": decision.query_type,
                    "n_subquerys": len(decision.subquerys),
                },
                timing_ms=_elapsed(t0),
            ))
        else:
            decision = RouteDecision.fallback(user_input, top_k=fallback_top_k)
            trace.stages.append(StageRecord(
                stage_name="planner_llm",
                status="SKIPPED",
                detail={
                    "reason": "estimator_not_activated",
                    "fallback_top_k": fallback_top_k,
                },
                timing_ms=_elapsed(t0),
            ))

        # ── 4. 检索 ──
        all_chunks: list[RetrievalResult] = []
        t0 = _now_ms()
        if self._dispatcher:
            response: RetrievalResponse = self._dispatcher.dispatch(decision)
            all_chunks = self._flatten_results(response)
        elif hasattr(self, "_legacy_retriever"):
            all_chunks = self._legacy_retrieve(user_input, decision)
        else:
            logger.warning("检索器未加载，跳过检索")
            rag_context = "[未选择知识库，跳过检索]"
            messages = self._build_messages(user_input, history or [], rag_context)
            response_text = self._call_llm(messages)
            trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
            self.trace_store.record(trace)
            return {
                "response": response_text,
                "route": self._route_summary(decision),
                "retrieval_context": rag_context,
                "rerank_scores": None,
                "cost": self.cost_tracker.session_summary(),
                "trace_id": trace.trace_id,
            }
        trace.stages.append(StageRecord(
            stage_name="retrieval",
            status="PASSED",
            detail={
                "mode": self.mode,
                "n_results": len(all_chunks),
                "top_k": decision.subquerys[0].subsubquerys[0].top_k
                if decision.subquerys and decision.subquerys[0].subsubquerys
                else fallback_top_k,
            },
            timing_ms=_elapsed(t0),
        ))

        # ── 5. Rerank（精排） ──
        t0 = _now_ms()
        rerank_scores = None
        if self.rerank_enabled and self._reranker and all_chunks:
            top_k = DIFFICULTY_TOP_K.get(decision.difficulty, 8)
            reranked = self._reranker.rerank(user_input, all_chunks, top_k)
            rerank_scores = [
                {"chunk_id": c.chunk.chunk_id, "ce_score": c.score}
                for c in reranked
            ]
            context_chunks = reranked
        else:
            all_chunks.sort(key=lambda x: x.score, reverse=True)
            context_chunks = all_chunks[: self.top_k]

        trace.stages.append(StageRecord(
            stage_name="reranker",
            status="PASSED" if self.rerank_enabled else "SKIPPED",
            detail={
                "enabled": self.rerank_enabled,
                "n_context_chunks": len(context_chunks),
            },
            timing_ms=_elapsed(t0),
        ))

        # ── 6. 构建上下文 ──
        rag_context = self._format_context(context_chunks)

        # ── 7. LLM 生成 ──
        t0 = _now_ms()
        messages = self._build_messages(user_input, history or [], rag_context)
        response_text = self._call_llm(messages)
        trace.stages.append(StageRecord(
            stage_name="llm",
            status="PASSED",
            detail={
                "n_input_tokens": len(str(messages)),
                "n_output_chars": len(response_text),
            },
            timing_ms=_elapsed(t0),
        ))

        trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
        self.trace_store.record(trace)

        return {
            "response": response_text,
            "route": self._route_summary(decision),
            "retrieval_context": rag_context,
            "rerank_scores": rerank_scores,
            "cost": self.cost_tracker.session_summary(),
            "trace_id": trace.trace_id,
        }

    # ── 流式核心流程 ────────────────────────────────────────────

    async def process_stream(self, user_input: str, history: Optional[list[dict]] = None):
        """流式 RAG 处理，逐步 yield 结构化事件。

        事件格式：
            {"type": "route",        ...}   路由决策
            {"type": "context",      ...}   检索上下文摘要
            {"type": "token",        ...}   LLM token
            {"type": "estimator",    ...}   PlannerEstimator 判定结果
            {"type": "trace_id",     ...}   全链路追踪 ID
            {"type": "done",         ...}   完成

        Args:
            user_input: 用户输入文本
            history: 历史消息列表

        Yields:
            结构化事件 dict
        """
        trace = PipelineTrace(
            trace_id=f"trc_{uuid.uuid4().hex[:12]}",
            query=user_input,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        fallback_top_k = self.config.get("planner_llm.fallback_top_k", 30)

        # ── 1. LegalPreFilter ──
        t0 = _now_ms()
        filter_result = self.pre_filter.filter(user_input)
        trace.stages.append(StageRecord(
            stage_name="legal_pre_filter",
            status="BLOCKED" if not filter_result.need_rag else "PASSED",
            detail={"skip_reason": filter_result.skip_reason},
            timing_ms=_elapsed(t0),
        ))
        if not filter_result.need_rag:
            response_text = self._direct_reply(filter_result.skip_reason)
            trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
            self.trace_store.record(trace)
            yield {"type": "done", "response": response_text,
                   "route": {"need_rag": False, "skip_reason": filter_result.skip_reason},
                   "retrieval_context": None, "rerank_scores": None,
                   "cost": self.cost_tracker.session_summary(),
                   "trace_id": trace.trace_id}
            return

        # ── 2. PlannerEstimator ──
        t0 = _now_ms()
        activated = False
        if self.planner_estimator:
            activated = self.planner_estimator.should_activate(user_input)
            proba = self.planner_estimator.predict_proba(user_input)
            trace.stages.append(StageRecord(
                stage_name="planner_estimator",
                status="ACTIVATED" if activated else "NOT_ACTIVATED",
                detail=proba,
                timing_ms=_elapsed(t0),
            ))
            yield {"type": "estimator", "proba": proba}
        else:
            activated = True
            trace.stages.append(StageRecord(
                stage_name="planner_estimator",
                status="SKIPPED",
                detail={"reason": "estimator_not_loaded"},
                timing_ms=_elapsed(t0),
            ))

        # ── 3. PlannerLLM / Fallback ──
        t0 = _now_ms()
        if activated:
            decision = self.planner_llm.route(user_input, filter_result)
            trace.stages.append(StageRecord(
                stage_name="planner_llm",
                status="PASSED",
                detail={
                    "difficulty": decision.difficulty,
                    "query_type": decision.query_type,
                },
                timing_ms=_elapsed(t0),
            ))
        else:
            decision = RouteDecision.fallback(user_input, top_k=fallback_top_k)
            trace.stages.append(StageRecord(
                stage_name="planner_llm",
                status="SKIPPED",
                detail={"reason": "estimator_not_activated"},
                timing_ms=_elapsed(t0),
            ))

        yield {"type": "trace_id", "trace_id": trace.trace_id}
        yield {"type": "route", "route": self._route_summary(decision)}

        # ── 4. 检索 ──
        all_chunks: list[RetrievalResult] = []
        t0 = _now_ms()
        if self._dispatcher:
            response: RetrievalResponse = self._dispatcher.dispatch(decision)
            all_chunks = self._flatten_results(response)
        elif hasattr(self, "_legacy_retriever"):
            all_chunks = self._legacy_retrieve(user_input, decision)
        else:
            rag_context = "[未选择知识库，跳过检索]"
            messages = self._build_messages(user_input, history or [], rag_context)
            response_text = self._call_llm(messages)
            trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
            self.trace_store.record(trace)
            yield {"type": "done", "response": response_text,
                   "route": self._route_summary(decision),
                   "retrieval_context": rag_context, "rerank_scores": None,
                   "cost": self.cost_tracker.session_summary(),
                   "trace_id": trace.trace_id}
            return
        trace.stages.append(StageRecord(
            stage_name="retrieval",
            status="PASSED",
            detail={"n_results": len(all_chunks), "mode": self.mode},
            timing_ms=_elapsed(t0),
        ))

        # ── 5. Rerank ──
        t0 = _now_ms()
        rerank_scores = None
        if self.rerank_enabled and self._reranker and all_chunks:
            top_k = DIFFICULTY_TOP_K.get(decision.difficulty, 8)
            reranked = self._reranker.rerank(user_input, all_chunks, top_k)
            rerank_scores = [
                {"chunk_id": c.chunk.chunk_id, "ce_score": c.score}
                for c in reranked
            ]
            context_chunks = reranked
        else:
            all_chunks.sort(key=lambda x: x.score, reverse=True)
            context_chunks = all_chunks[: self.top_k]
        trace.stages.append(StageRecord(
            stage_name="reranker",
            status="PASSED" if self.rerank_enabled else "SKIPPED",
            detail={"n_context_chunks": len(context_chunks)},
            timing_ms=_elapsed(t0),
        ))

        # ── 6. 构建上下文 ──
        rag_context = self._format_context(context_chunks)
        yield {"type": "context", "retrieval_context": rag_context,
               "rerank_scores": rerank_scores,
               "chunk_count": len(context_chunks)}

        # ── 7. LLM 流式生成 ──
        t0 = _now_ms()
        messages = self._build_messages(user_input, history or [], rag_context)
        full_response = []
        async for token in self.llm.generate_stream_async(messages):
            full_response.append(token)
            yield {"type": "token", "text": token}

        response_text = "".join(full_response)
        trace.stages.append(StageRecord(
            stage_name="llm",
            status="PASSED",
            detail={"n_output_chars": len(response_text)},
            timing_ms=_elapsed(t0),
        ))
        trace.total_timing_ms = sum(s.timing_ms for s in trace.stages)
        self.trace_store.record(trace)
        yield {"type": "done", "response": response_text,
               "route": self._route_summary(decision),
               "retrieval_context": rag_context,
               "rerank_scores": rerank_scores,
               "cost": self.cost_tracker.session_summary()}

    # ── 内部方法 ──────────────────────────────────────────────────

    def _flatten_results(
        self, response: RetrievalResponse
    ) -> list[RetrievalResult]:
        """展平 RetrievalResponse 为统一列表 + 按 chunk_id 去重 + 按分数排序。

        跨 SubQuery 去重时保留每个 chunk_id 的最高分。
        返回按检索分数降序排列的列表，由调用方决定截断长度。
        """
        seen: dict[str, RetrievalResult] = {}
        for sq in response.subquery_results:
            for ssr in sq.subsubresults:
                for c in ssr.chunks:
                    cid = c.chunk.chunk_id
                    if cid not in seen or c.score > seen[cid].score:
                        seen[cid] = c
        results = list(seen.values())
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _legacy_retrieve(
        self, query: str, decision
    ) -> list[RetrievalResult]:
        """旧索引兼容检索（过渡期）。"""
        retriever = getattr(self, "_legacy_retriever", None)
        if retriever is None:
            return []

        # 从 RouteDecision 中提取查询
        queries = []
        for sq in decision.subquerys:
            for ssq in sq.subsubquerys:
                queries.append(ssq.subsubquery)

        if not queries:
            queries = [query]

        seen: dict[str, float] = {}
        for q in queries:
            try:
                results = retriever.retrieve(q, top_k=self.top_k)
                for r in results:
                    cid = r.chunk.chunk_id
                    if cid not in seen or r.score > seen[cid]:
                        seen[cid] = r.score
            except Exception as e:
                logger.warning("检索出错 (query=%s): %s", q[:50], e)

        if not seen:
            return []

        # re-fetch 完整结果
        all_results: list[RetrievalResult] = []
        for q in queries:
            try:
                results = retriever.retrieve(q, top_k=self.top_k * 2)
                all_results.extend(
                    r for r in results if r.chunk.chunk_id in seen
                )
            except Exception:
                pass

        seen_ids = set()
        deduped = []
        for r in all_results:
            if r.chunk.chunk_id not in seen_ids:
                seen_ids.add(r.chunk.chunk_id)
                deduped.append(r)

        deduped.sort(key=lambda x: x.score, reverse=True)
        return deduped[: self.top_k]

    def _format_context(
        self, results: list[RetrievalResult]
    ) -> str:
        """将检索结果格式化为 LLM 上下文。"""
        lines = ["以下是与用户问题相关的知识库内容：", ""]
        for i, r in enumerate(results, 1):
            source = r.chunk.metadata.get("source", r.chunk.doc_id)
            heading = (
                " > ".join(r.chunk.heading_path)
                if r.chunk.heading_path
                else ""
            )
            lines.append(f"[{i}] {r.chunk.text}")
            if heading:
                lines.append(f"    来源: {source} | 章节: {heading}")
            else:
                lines.append(f"    来源: {source}")
            lines.append("")
        return "\n".join(lines)

    def _build_messages(
        self,
        user_input: str,
        history: list[dict],
        rag_context: str,
    ) -> list[dict]:
        """构建 LLM 消息列表。"""
        messages = []

        # 系统 prompt
        system_content = self._load_system_prompt()
        messages.append({"role": "system", "content": system_content})

        # 历史消息
        messages.extend(history)

        # RAG 上下文
        if rag_context:
            messages.append({"role": "system", "content": rag_context})

        # 当前用户输入
        messages.append({"role": "user", "content": user_input})

        return messages

    def _load_system_prompt(self) -> str:
        """读取 agent 系统 prompt。"""
        path = Path("prompts/agent_prompt.txt")
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "你是一个基于知识库的 RAG 助手。"
            "请根据提供的上下文信息回答问题，"
            "如果上下文信息不足以回答问题，请如实告知。"
        )

    def _call_llm(self, messages: list[dict]) -> str:
        """调用 LLM 生成回复。"""
        try:
            resp = self.llm.generate(messages)
            return resp.choices[0].message.content
        except Exception as e:
            logger.error("LLM 生成出错: %s", e)
            return f"[生成回复时出错: {e}]"

    def _direct_reply(self, skip_reason: str) -> str:
        """PreFilter 短路时的直接回复。"""
        replies = {
            "empty_query": "请输入有效的问题。",
            "greeting": "你好！有什么我可以帮你的吗？",
            "nonsense": "请提出有效的问题。",
            "irrelevant": "这个问题与当前知识库无关，请尝试其他问题。",
        }
        return replies.get(skip_reason, "")

    @staticmethod
    def _route_summary(decision) -> dict:
        """提取路由决策摘要。"""
        return {
            "need_rag": decision.need_rag,
            "difficulty": decision.difficulty,
            "query_type": decision.query_type,
            "reasoning": decision.reasoning,
        }


# ── Trace 辅助函数 ──────────────────────────────────────────


def _now_ms() -> float:
    """返回当前时间（毫秒）。"""
    import time
    return time.monotonic()


def _elapsed(start: float) -> float:
    """返回从 start 到现在的毫秒数。"""
    import time
    return round((time.monotonic() - start) * 1000, 2)


def _retrieval_result_to_trace_chunk(rr, rank: int) -> TraceChunk:
    """将 RetrievalResult 转为 TraceChunk。"""
    chunk = rr.chunk
    related_available = {
        "parent": bool(getattr(chunk, "parent_chunk_id", None)),
        "children": bool(getattr(chunk, "child_chunk_ids", [])),
        "prev": bool(getattr(chunk, "prev_chunk_id", None)),
        "next": bool(getattr(chunk, "next_chunk_id", None)),
    }
    return TraceChunk(
        rank=rank,
        chunk_id=getattr(chunk, "chunk_id", ""),
        doc_id=getattr(chunk, "doc_id", ""),
        text=(getattr(chunk, "text", "") or "")[:500],
        score=rr.score,
        rerank_score=None,
        retrieval_type=getattr(rr, "retrieval_type", "qdrant"),
        chunk_level=getattr(chunk, "chunk_level", "single"),
        parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
        child_chunk_ids=getattr(chunk, "child_chunk_ids", []),
        prev_chunk_id=getattr(chunk, "prev_chunk_id", None),
        next_chunk_id=getattr(chunk, "next_chunk_id", None),
        related_available=related_available,
        heading_path=getattr(chunk, "heading_path", []),
        metadata=getattr(chunk, "metadata", {}),
    )


def _sub_trace_to_dict(st: SubTraceResult) -> dict:
    """将 SubTraceResult 转为可序列化 dict。"""
    return {
        "subquery_id": st.subquery_id,
        "subquery": st.subquery,
        "subsubresults": [
            {
                "subsubquery_id": sst.subsubquery_id,
                "subsubquery": sst.subsubquery,
                "strategy": sst.strategy,
                "top_k": sst.top_k,
                "chunks": [c.__dict__ for c in sst.chunks],
            }
            for sst in st.subsubresults
        ],
    }
