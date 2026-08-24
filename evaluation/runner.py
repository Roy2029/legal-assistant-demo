"""实验执行引擎。

加载 ExperimentConfig，为每个 Run 执行完整的检索-评估-聚合流程，
支持缓存、进度显示、分组统计和结果持久化。

用法:
    config = ExperimentConfig.from_yaml("experiments/my-exp/experiment.yaml")
    embedder = HuggingFaceEmbeddingModel("local_model/bge-base-zh")
    runner = ExperimentRunner(config, embedder)
    runner.run()
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from statistics import mean, stdev
from pathlib import Path
from typing import Any, Optional

from offline_core.modules import BaseEmbeddingModel
from offline_core.store import QdrantStore, QdrantConfig

from evaluation.cache import RecallCache, CachedChunk
from evaluation.registry import ExperimentsRegistry
from evaluation.config import (
    DatasetRef,
    ExperimentConfig,
    IndexRef,
    PipelineConfig,
    RunConfig,
)
from evaluation.metrics import compute_all_metrics, compute_metrics_summary, micro_f1_at_k
from evaluation.pipeline import EvalPipeline, RetrievedChunk
from offline_core.data_model import RetrievalResult, Chunk as _Chunk

logger = logging.getLogger(__name__)


class ExperimentRunner:
    """实验执行引擎。

    执行一次实验的所有 Run（每个 Run 是 pipeline 的一种参数化），
    收集逐 query 指标并聚合为 summary。
    """

    def __init__(
        self,
        config: ExperimentConfig,
        embedder: BaseEmbeddingModel,
        llm: object = None,
        fallback_llm: object = None,
        exp_dir: Optional[str | Path] = None,
        kb_vocab_path: Optional[str] = None,
    ):
        """
        Args:
            config: 已加载的实验配置
            embedder: Embedding 模型实例
            llm: 可选的 LLM 实例（router 启用时需要）
            fallback_llm: 可选的备用 LLM（主 LLM 失败时使用）
            exp_dir: 实验目录（results/reports/cache 的父目录），
                     默认为 experiments/<config.name>/
            kb_vocab_path: PreFilter 词表路径（可选）
        """
        self.config = config
        self.embedder = embedder
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.kb_vocab_path = kb_vocab_path

        # 实验目录
        if exp_dir is None:
            exp_dir = Path("experiments") / config.name
        self.exp_dir = Path(exp_dir)
        self.results_dir = self.exp_dir / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # QdrantStore
        self.store = self._create_store(config.index)

        # 维度校验：确保 embedding 模型输出维度与 Qdrant collection 一致
        self._verify_dimension()

        # 加载数据集
        self.queries, self.qrels = self._load_dataset(config.dataset)

        logger.info(
            "ExperimentRunner 初始化完成: %d 条 query, %d 条 qrels, %d 个 Run",
            len(self.queries),
            sum(len(v) for v in self.qrels.values()),
            len(config.runs),
        )

    # ── 初始化工具 ─────────────────────────────────────────────────

    @staticmethod
    def _create_store(index: IndexRef) -> QdrantStore:
        """从 IndexRef 创建 QdrantStore（含 BM25 encoder 自动加载）。"""
        qdrant_config = QdrantConfig(
            mode="embedded",
            path=index.path,
            collection_name=index.db_name,
        )
        store = QdrantStore(qdrant_config)

        # 加载 BM25 encoder（如果存在）
        bm25_path = Path(index.path) / "bm25_encoder.pkl"
        if bm25_path.exists():
            from offline_core.store import BM25Encoder
            store._bm25_encoder = BM25Encoder.load(str(bm25_path))
            store._collection_ready = False  # 触发 collection 验证
            store._indexes_created = True
            logger.info("BM25 encoder 已加载: %s", bm25_path)

        return store

    def _verify_dimension(self) -> None:
        """校验 embedding 模型输出维度与 Qdrant collection 一致。

        维度不匹配时检索会静默返回空结果（无报错），因此需要启动时主动检查。
        """
        model_dim = self.embedder.dimension
        try:
            collection_info = self.store.client.get_collection(
                self.store.collection_name
            )
            vectors_config = collection_info.config.params.vectors
            if vectors_config and "dense" in vectors_config:
                collection_dim = vectors_config["dense"].size
            else:
                # 无法确定 collection 维度时跳过检查
                return

            if model_dim != collection_dim:
                raise ValueError(
                    f"Embedding 模型维度 ({model_dim}) 与 Qdrant collection "
                    f"'{self.store.collection_name}' 维度 ({collection_dim}) 不匹配。\n"
                    f"请使用与 collection 维度一致的模型，"
                    f"或为当前模型重新构建索引。"
                )
            logger.info(
                "维度校验通过: model=%d, collection=%d",
                model_dim, collection_dim,
            )
        except ValueError:
            raise
        except Exception as e:
            logger.warning("无法校验 collection 维度: %s", e)

    @staticmethod
    def _load_dataset(
        dataset: DatasetRef,
    ) -> tuple[list[dict], dict[str, set[str]]]:
        """加载 queries.json 和 qrels.json。

        Returns:
            (queries_list, qrels_map)
            queries_list: 每项为 {query_id, query, query_type, difficulty, ...}
            qrels_map: {query_id: set[chunk_id]} 仅保留 relevance > 0 的 chunk
        """
        with open(dataset.queries_path, "r", encoding="utf-8") as f:
            queries_raw = json.load(f)

        with open(dataset.qrels_path, "r", encoding="utf-8") as f:
            qrels_raw = json.load(f)

        # 构建 qrels map：每个 query 对应的相关 chunk_id 集合
        qrels: dict[str, set[str]] = defaultdict(set)
        for entry in qrels_raw:
            qid = entry["query_id"]
            if entry.get("relevance", 0) > 0:
                qrels[qid].add(entry["chunk_id"])

        # 过滤出有 qrels 的 query（未标注相关 chunk 的跳过并警告）
        queries: list[dict] = []
        for q in queries_raw:
            qid = q["query_id"]
            if qid not in qrels or len(qrels[qid]) == 0:
                logger.warning("query '%s' 无相关 chunk（qrels 为空），跳过", qid)
                continue
            queries.append(q)

        logger.info(
            "数据集加载: %d/%d 条 query 有效",
            len(queries), len(queries_raw),
        )
        return queries, dict(qrels)

    # ── 主执行入口 ─────────────────────────────────────────────────

    def run(
        self,
        run_names: Optional[list[str]] = None,
        dry_run: bool = False,
        use_cache: bool = True,
    ) -> dict[str, dict]:
        """执行实验。

        Args:
            run_names: 要执行的 Run 名称列表（None = 全部）
            dry_run: 仅验证配置、打印摘要，不实际执行
            use_cache: 是否使用检索缓存

        Returns:
            {run_name: result_dict} 每个 Run 的执行结果
        """
        # 筛选要执行的 Run
        runs_to_exec = self.config.runs
        if run_names:
            runs_to_exec = [r for r in self.config.runs if r.name in run_names]
            if not runs_to_exec:
                raise ValueError(f"未找到指定的 Run: {run_names}")

        if dry_run:
            self._dry_run(runs_to_exec)
            return {}

        # 初始化缓存
        cache: Optional[RecallCache] = None
        if use_cache:
            cache = RecallCache(self.exp_dir / "cache" / "recall_cache.db")

        all_results: dict[str, dict] = {}

        # ── 初始化元数据注册表 ────────────────────────────────────
        registry = ExperimentsRegistry()
        exp_id = registry.create_experiment(
            name=self.config.name,
            description=self.config.description,
            config=self.config,
            embedding_model=getattr(self.embedder, "model_name", ""),
            llm_model=self.llm.__class__.__name__ if self.llm else "",
            code_version=ExperimentsRegistry.get_code_version(),
        )

        exp_start_time = time.perf_counter()
        error_occurred = False

        for run_cfg in runs_to_exec:
            logger.info("=" * 60)
            logger.info("执行 Run: %s", run_cfg.name)
            if run_cfg.description:
                logger.info("  描述: %s", run_cfg.description)

            # 注册 Run
            run_id = registry.create_run(
                exp_id, run_cfg.name, run_cfg.description,
                pipeline_config=run_cfg.pipeline.model_dump(),
                num_queries_total=len(self.queries),
            )

            try:
                run_result = self._execute_run(run_cfg, cache)

                # 更新 registry
                duration = run_result.get("_duration", 0.0)
                registry.update_run_progress(
                    run_id,
                    run_result["num_queries"],
                    len(self.queries),
                    run_result.get("cache_hits", 0),
                    run_result.get("cache_misses", 0),
                )
                registry.complete_run(
                    run_id, duration, run_result.get("summary"),
                )

            except Exception as e:
                error_occurred = True
                logger.exception("Run '%s' 执行失败: %s", run_cfg.name, e)
                registry.update_run_status(run_id, "error", str(e))
                raise

            self._save_result(run_cfg.name, run_result)
            all_results[run_cfg.name] = run_result

            # 打印简要统计
            self._print_run_summary(run_cfg.name, run_result)

        # ── 实验结束 ──────────────────────────────────────────────
        exp_duration = time.perf_counter() - exp_start_time
        if error_occurred:
            registry.update_experiment_status(exp_id, "error")
        else:
            registry.complete_experiment(exp_id, exp_duration)
        registry.update_experiment_queries(exp_id, len(self.queries))

        # 生成 metadata.json
        registry.write_metadata_json(exp_id, self.exp_dir)

        # 最终缓存统计
        if cache:
            h, m = cache.stats()
            logger.info("缓存统计 — hits: %d, misses: %d", h, m)

        return all_results

    # ── 单 Run 执行 ────────────────────────────────────────────────

    def _execute_run(
        self,
        run_cfg: RunConfig,
        cache: Optional[RecallCache],
    ) -> dict:
        """执行单个 Run：逐 query 检索 + 评估。"""
        pipeline = EvalPipeline(
            config=run_cfg.pipeline,
            store=self.store,
            embedding_model=self.embedder,
            llm=self.llm,
            fallback_llm=self.fallback_llm,
            kb_vocab_path=self.kb_vocab_path,
        )

        recall_config_json = json.dumps(
            {
                "mode": run_cfg.pipeline.recall.mode,
                "top_k": run_cfg.pipeline.recall.top_k,
                "fusion": run_cfg.pipeline.recall.fusion,
                "router": run_cfg.pipeline.router.enabled,
                "router_version": run_cfg.pipeline.router.version if run_cfg.pipeline.router.enabled else None,
                "router_fallback_top_k": run_cfg.pipeline.recall.top_k if run_cfg.pipeline.router.enabled else None,
                "prefilter": run_cfg.pipeline.prefilter.enabled,
            },
            sort_keys=True,
        )

        per_query_metrics: list[dict[str, Any]] = []
        q_count = 0
        cache_hits = 0
        cache_misses = 0
        _run_start = time.perf_counter()

        ks = self.config.metrics.ks

        for q in self._progress(self.queries, desc=f"  [{run_cfg.name}]"):
            qid = q["query_id"]
            query_text = q["query"]
            relevant_ids = self.qrels.get(qid, set())

            if not relevant_ids:
                continue

            # 尝试缓存
            chunks: list[CachedChunk] = []
            result = None
            if cache is not None:
                cached = cache.get(query_text, recall_config_json)
                if cached is not None:
                    chunks = cached
                    cache_hits += 1
                else:
                    cache_misses += 1

            if not chunks:
                # 执行检索（skip_rerank=True：确保缓存仅存 recall 结果，不含 reranker 处理）
                result = pipeline.retrieve(query_text, skip_rerank=True)
                chunks = [
                    CachedChunk(
                        chunk_id=r.chunk_id,
                        score=r.score,
                        content=r.content,
                        doc_id=r.doc_id,
                    )
                    for r in result.chunks
                ]
                # 写入缓存
                if cache is not None and chunks:
                    cache.put(query_text, recall_config_json, chunks)

            # Reranker：缓存命中后单独执行（确保 reranker 始终生效，不受缓存影响）
            rerank_time = 0.0
            if run_cfg.pipeline.rerank.enabled and pipeline.reranker is not None:
                t_rerank = time.perf_counter()
                _reranked = pipeline.reranker.rerank(
                    query=query_text,
                    candidates=self._chunks_to_retrieval_results(chunks),
                    top_k=run_cfg.pipeline.rerank.top_k,
                )
                chunks = [
                    CachedChunk(
                        chunk_id=c.chunk.chunk_id,
                        score=c.score,
                        content=c.chunk.text,
                        doc_id=c.chunk.doc_id,
                    )
                    for c in _reranked
                ]
                rerank_time = time.perf_counter() - t_rerank

            # 计算指标
            retrieved_ids = [c.chunk_id for c in chunks]
            metrics = compute_all_metrics(retrieved_ids, relevant_ids, ks)

            # 构造 timing（合并 pipeline 耗时 + runner 层 reranker 耗时）
            if result is not None:
                timing_data = dict(result.timing)
                timing_data["rerank"] = rerank_time
            else:
                timing_data = {"rerank": rerank_time, "total": rerank_time}

            # 附加元数据
            record = {
                "query_id": qid,
                "query": query_text,
                "query_type": q.get("query_type", []),
                "difficulty": q.get("difficulty", "unknown"),
                "num_relevant": len(relevant_ids),
                "num_retrieved": len(chunks),
                "retrieved_ids": retrieved_ids,
                "timing": timing_data,
                "route_strategies": result.route_strategies if result else [],
                "route_difficulty": result.route_difficulty if result else "",
                "route_decision_detail": result.route_decision_detail if result else "",
                **metrics,
            }
            per_query_metrics.append(record)
            q_count += 1

        # 聚合统计
        summary = compute_metrics_summary(
            [{k: v for k, v in m.items() if isinstance(v, (int, float))}
             for m in per_query_metrics]
        )

        # ── latency 聚合 ──────────────────────────────────────────
        timing_fields = ["prefilter", "router", "recall", "rerank", "total"]
        for field in timing_fields:
            values = [m.get("timing", {}).get(field, 0.0) for m in per_query_metrics]
            if any(v > 0 for v in values):
                sorted_vals = sorted(values)
                n = len(sorted_vals)
                summary[f"latency_{field}"] = {
                    "mean": mean(values),
                    "std": stdev(values) if len(values) > 1 else 0.0,
                    "min": sorted_vals[0],
                    "max": sorted_vals[-1],
                    "p50": sorted_vals[n // 2],
                    "p90": sorted_vals[min(int(n * 0.9), n - 1)],
                    "p95": sorted_vals[min(int(n * 0.95), n - 1)],
                    "p99": sorted_vals[min(int(n * 0.99), n - 1)],
                }

        # ── micro-F1 聚合 ─────────────────────────────────────────
        all_retrieved = [
            m.get("retrieved_ids", []) for m in per_query_metrics
        ]
        all_relevant = [
            self.qrels.get(m.get("query_id", ""), set()) for m in per_query_metrics
        ]
        for k in self.config.metrics.ks:
            summary[f"micro_F1@{k}"] = {"mean": micro_f1_at_k(all_retrieved, all_relevant, k)}
            # 也补充 macro_F1@K 显式别名（与 F1@K 均值等价）
            if f"F1@{k}" in summary:
                summary[f"macro_F1@{k}"] = summary[f"F1@{k}"]

        # 分组统计
        grouped = self._group_metrics(per_query_metrics)

        return {
            "run_name": run_cfg.name,
            "description": run_cfg.description,
            "pipeline": run_cfg.pipeline.model_dump(),
            "num_queries": q_count,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "summary": summary,
            "by_group": grouped,
            "per_query": per_query_metrics,
            "_duration": time.perf_counter() - _run_start,
        }

    # ── 聚合统计 ───────────────────────────────────────────────────

    def _group_metrics(
        self, per_query: list[dict[str, Any]]
    ) -> dict[str, dict[str, dict]]:
        """按配置的 group_by 维度分组聚合指标。

        Returns:
            {dimension: {group_value: summary_dict}}
        """
        group_by = self.config.metrics.group_by
        if not group_by:
            return {}

        numeric_metrics = [
            {k: v for k, v in m.items() if isinstance(v, (int, float))}
            for m in per_query
        ]

        result: dict[str, dict[str, dict]] = {}
        for dim in group_by:
            groups: dict[str, list[dict]] = defaultdict(list)
            for i, m in enumerate(per_query):
                vals = m.get(dim, [])
                if isinstance(vals, list):
                    for v in vals:
                        groups[v].append(numeric_metrics[i])
                else:
                    groups[str(vals)].append(numeric_metrics[i])

            result[dim] = {}
            for group_val, group_metrics in groups.items():
                if group_metrics:
                    summary = compute_metrics_summary(group_metrics)
                    summary["count"] = len(group_metrics)
                    result[dim][group_val] = summary

        return result

    # ── 结果持久化 ─────────────────────────────────────────────────

    def _save_result(self, run_name: str, result: dict) -> None:
        """将 Run 结果写入 JSON 文件。"""
        out_path = self.results_dir / f"{run_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info("  结果已保存: %s", out_path)

    # ── Dry Run ────────────────────────────────────────────────────

    def _dry_run(self, runs: list[RunConfig]) -> None:
        """打印将要执行的计划，不实际执行。"""
        print(f"\n{'='*60}")
        print(f"DRY RUN — 实验: {self.config.name}")
        print(f"数据集: {self.config.dataset.queries_path}")
        print(f"         {self.config.dataset.qrels_path}")
        print(f"索引:   {self.config.index.path} (collection={self.config.index.db_name})")
        print(f"指标 K: {self.config.metrics.ks}")
        print(f"分组:   {self.config.metrics.group_by or '(无)'}")
        print(f"有效 query 数: {len(self.queries)}")
        print(f"\n待执行 Run ({len(runs)}):")
        for r in runs:
            p = r.pipeline
            flags = []
            if p.prefilter.enabled:
                flags.append("prefilter")
            if p.router.enabled:
                flags.append("router")
            if p.rerank.enabled:
                flags.append(f"rerank(top_k={p.rerank.top_k})")
            flags_str = " → ".join(flags) if flags else "recall only"
            print(
                f"  [{r.name}] recall={p.recall.mode}(top_k={p.recall.top_k}) "
                f"| {flags_str}"
            )
            if r.description:
                print(f"           {r.description}")
        print(f"{'='*60}\n")

    # ── 终端输出 ───────────────────────────────────────────────────

    @staticmethod
    def _print_run_summary(run_name: str, result: dict) -> None:
        """打印 Run 的简要统计摘要。"""
        s = result.get("summary", {})
        ks_keys = [k for k in s.keys() if "Recall@" in k]
        summary_keys = ks_keys + ["MRR", "NDCG@10"]

        parts = []
        for k in summary_keys:
            if k in s:
                parts.append(f"{k}={s[k]['mean']:.4f}")

        cache_hits = result.get("cache_hits", 0)
        cache_misses = result.get("cache_misses", 0)
        cache_str = ""
        if cache_hits + cache_misses > 0:
            cache_str = f" | 缓存: {cache_hits} hits / {cache_misses} misses"

        print(
            f"  [{run_name}] {result['num_queries']} 条 query | "
            + " | ".join(parts)
            + cache_str
        )

    @staticmethod
    def _progress(iterable, desc: str = "", **kwargs):
        """进度条包装（自动选择 tqdm 或 fallback）。"""
        try:
            from tqdm import tqdm
            return tqdm(iterable, desc=desc, **kwargs)
        except ImportError:
            logger.info("%s (共 %d 项)", desc, len(list(iterable)))
            return iterable

    @staticmethod
    def _chunks_to_retrieval_results(
        chunks: list[CachedChunk],
    ) -> list[RetrievalResult]:
        """将 CachedChunk 转回 RetrievalResult，供 reranker 消费。"""
        return [
            RetrievalResult(
                chunk=_Chunk(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    text=c.content,
                    block_ids=[],
                    order=0,
                ),
                score=c.score,
                retrieval_type="hybrid",
            )
            for c in chunks
        ]
