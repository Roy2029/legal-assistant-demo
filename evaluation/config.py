"""Pydantic 实验配置模型。

定义 Experiment YAML 的完整结构，包括 PipelineConfig、ExperimentConfig、
DatasetRef、IndexRef 和 RunConfig。支持从 YAML 文件加载和校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, model_validator


# ══════════════════════════════════════════════════════════════════════════════
# 辅助引用模型
# ══════════════════════════════════════════════════════════════════════════════


class DatasetRef(BaseModel):
    """数据集引用：指向 queries.json 和 qrels.json 的路径。"""

    queries_path: str = Field(..., description="queries.json 文件路径")
    qrels_path: str = Field(..., description="qrels.json 文件路径")


class IndexRef(BaseModel):
    """索引引用：指向 Qdrant 存储路径和 collection 名称。"""

    path: str = Field(..., description="Qdrant 数据目录路径")
    db_name: str = Field(default="default", description="Qdrant collection 名称")


class MetricsConfig(BaseModel):
    """指标配置。"""

    ks: list[int] = Field(
        default=[1, 3, 5, 10, 20],
        description="Recall@k / Precision@k / NDCG@k 的 k 值列表",
    )
    group_by: list[str] = Field(
        default=[],
        description="分组统计维度，如 ['query_type', 'difficulty']",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline 阶段配置
# ══════════════════════════════════════════════════════════════════════════════


class PreFilterConfig(BaseModel):
    """PreFilter 阶段配置（规则层，零 LLM 开销）。"""

    enabled: bool = Field(default=False, description="是否启用 QueryPreFilter")


class RouterConfig(BaseModel):
    """Router 阶段配置（LLM 路由 + 改写，合并为同一 LLM 调用）。"""

    enabled: bool = Field(default=False, description="是否启用 LLM 路由/改写")
    version: str = Field(default="V1", description="Router 版本: 'V1' | 'V2'")


class RecallConfig(BaseModel):
    """Recall 阶段配置：召回策略和参数。"""

    mode: str = Field(
        default="hybrid",
        description="检索模式: 'dense' | 'sparse' | 'hybrid'",
    )
    top_k: int = Field(default=20, description="召回阶段返回的最大 chunk 数")
    fusion: str = Field(
        default="rrf",
        description="Hybrid 模式下的融合策略: 'rrf' | 'dbsf'",
    )


class RerankConfig(BaseModel):
    """Rerank 阶段配置（CrossEncoder 精排）。"""

    enabled: bool = Field(default=False, description="是否启用精排")
    model_path: str = Field(
        default="local_model/bge-reranker-v2-m3",
        description="CrossEncoder 模型路径",
    )
    top_k: int = Field(default=10, description="精排后保留的最大 chunk 数")
    device: str = Field(default="cuda", description="推理设备: 'cpu' | 'cuda'")
    batch_size: int = Field(default=32, description="Batch 推理大小")


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline 总配置
# ══════════════════════════════════════════════════════════════════════════════


class PipelineConfig(BaseModel):
    """可组合的阶段式检索管线配置。

    四个阶段：prefilter → router → recall → rerank。
    每个阶段可独立开启/关闭和调参。recall 始终启用。
    """

    prefilter: PreFilterConfig = Field(default_factory=PreFilterConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    recall: RecallConfig = Field(default_factory=RecallConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)


# ══════════════════════════════════════════════════════════════════════════════
# Run 配置
# ══════════════════════════════════════════════════════════════════════════════


class RunConfig(BaseModel):
    """单个 Run 的配置：pipeline 的参数化变体。

    继承 baseline pipeline 配置，仅覆写差异字段。
    """

    name: str = Field(..., description="Run 名称（用作结果文件名）")
    description: str = Field(default="", description="Run 描述")
    pipeline: PipelineConfig = Field(
        default_factory=PipelineConfig,
        description="该 Run 的 pipeline 配置",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 顶层实验配置
# ══════════════════════════════════════════════════════════════════════════════


class ExperimentConfig(BaseModel):
    """顶层实验配置模型。

    一个 Experiment 包含：
    - 数据集引用（queries + qrels）
    - 索引引用（Qdrant 路径 + collection）
    - Pipeline 基线配置（各 Run 在此基础上覆写）
    - 多个参数化 Run
    - 指标配置

    使用示例:
        config = ExperimentConfig.from_yaml("experiments/my-exp/experiment.yaml")
    """

    name: str = Field(..., description="实验名称")
    description: str = Field(default="", description="实验描述")
    dataset: DatasetRef = Field(..., description="数据集引用")
    index: IndexRef = Field(..., description="索引引用")
    pipeline: PipelineConfig = Field(
        default_factory=PipelineConfig,
        description="Pipeline 基线配置（各 Run 默认继承）",
    )
    runs: list[RunConfig] = Field(..., description="参数化 Run 列表")
    metrics: MetricsConfig = Field(default_factory=MetricsConfig, description="指标配置")

    @model_validator(mode="after")
    def _validate_runs_non_empty(self) -> "ExperimentConfig":
        if not self.runs:
            raise ValueError("experiment.yaml 必须至少定义 1 个 run")
        run_names = [r.name for r in self.runs]
        if len(run_names) != len(set(run_names)):
            seen = set()
            dupes = {n for n in run_names if n in seen or seen.add(n)}
            raise ValueError(f"Run 名称重复: {dupes}")
        return self

    def validate_paths(self) -> list[str]:
        """验证数据集和索引路径存在（执行前调用）。

        Returns:
            错误信息列表（空列表 = 验证通过）
        """
        errors: list[str] = []
        for field_name, path_attr in [
            ("dataset.queries_path", self.dataset.queries_path),
            ("dataset.qrels_path", self.dataset.qrels_path),
            ("index.path", self.index.path),
        ]:
            p = Path(path_attr)
            if not p.exists():
                errors.append(f"{field_name} 路径不存在: {path_attr}")
        return errors

    # ── YAML 加载 ─────────────────────────────────────────────────

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        validate_paths: bool = False,
    ) -> "ExperimentConfig":
        """从 YAML 文件加载并校验实验配置。

        Args:
            path: experiment.yaml 文件路径
            validate_paths: 是否校验引用路径存在（默认 False，执行时单独校验）

        Returns:
            校验后的 ExperimentConfig 实例

        Raises:
            FileNotFoundError: YAML 文件不存在
            ValidationError: 配置校验失败
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"实验配置文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            raise ValueError(f"实验配置文件为空: {path}")

        # 处理 YAML anchors：PyYAML 的 safe_load 已自动解析
        # 这里处理 Run 的 pipeline 继承逻辑：
        # 如果 run 的 pipeline 配置不完整，从 baseline pipeline 继承
        baseline_pipeline = raw.get("pipeline", {})
        for run in raw.get("runs", []):
            if "pipeline" in run and baseline_pipeline:
                run_pipeline = run["pipeline"]
                # 深度合并：baseline 作为默认，run 覆写
                merged = _deep_merge_pipeline(baseline_pipeline, run_pipeline)
                run["pipeline"] = merged
            elif "pipeline" not in run and baseline_pipeline:
                run["pipeline"] = baseline_pipeline

        config = cls.model_validate(raw)

        if validate_paths:
            path_errors = config.validate_paths()
            if path_errors:
                from pydantic import ValidationError
                raise ValueError(
                    "路径校验失败:\n  " + "\n  ".join(path_errors)
                )

        return config

    # ── 摘要 ─────────────────────────────────────────────────────

    def summary(self) -> str:
        """返回实验配置摘要。"""
        lines = [
            f"实验: {self.name}",
            f"描述: {self.description or '(无)'}",
            f"数据集: {self.dataset.queries_path} / {self.dataset.qrels_path}",
            f"索引: {self.index.path} (collection={self.index.db_name})",
            f"指标 K 值: {self.metrics.ks}",
            f"分组维度: {self.metrics.group_by or '(无)'}",
            f"Run 数量: {len(self.runs)}",
        ]
        for run in self.runs:
            p = run.pipeline
            parts = [f"  recall={p.recall.mode}(top_k={p.recall.top_k})"]
            if p.router.enabled:
                parts.append("router=on")
            if p.prefilter.enabled:
                parts.append("prefilter=on")
            if p.rerank.enabled:
                parts.append(f"rerank(top_k={p.rerank.top_k})")
            lines.append(f"  [{run.name}] {' | '.join(parts)}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════


def _deep_merge_pipeline(base: dict, override: dict) -> dict:
    """深度合并 pipeline 配置：override 优先，base 作为默认。

    处理嵌套结构：prefilter / router / recall / rerank
    每个子配置也是 dict，需要递归合并。
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result
