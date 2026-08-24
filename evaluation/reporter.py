"""报告生成器。

读取实验目录下的 Run 结果 JSON 文件，生成三种格式的报告：
1. Markdown 对比报告（summary.md）
2. 结构化 JSON 对比数据（comparison.json）
3. 逐 query CSV 明细（per_query.csv）

同时支持终端 ASCII 表格输出。

用法:
    reporter = Reporter(config, "experiments/my-exp/")
    reporter.generate_all()
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from evaluation.config import ExperimentConfig

logger = logging.getLogger(__name__)


class Reporter:
    """报告生成器。"""

    def __init__(self, config: ExperimentConfig, exp_dir: str | Path):
        self.config = config
        self.exp_dir = Path(exp_dir)
        self.results_dir = self.exp_dir / "results"
        self.reports_dir = self.exp_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # 加载所有 result JSON
        self.results: dict[str, dict] = {}
        self._load_results()

    def _load_results(self) -> None:
        """加载 results/ 目录下的所有 JSON 结果文件。"""
        if not self.results_dir.exists():
            logger.warning("结果目录不存在: %s", self.results_dir)
            return

        for f in sorted(self.results_dir.glob("*.json")):
            run_name = f.stem
            with open(f, "r", encoding="utf-8") as fp:
                self.results[run_name] = json.load(fp)

        logger.info("加载了 %d 个 Run 结果", len(self.results))

    # ── 生成全部报告 ───────────────────────────────────────────────

    def generate_all(self) -> dict[str, str]:
        """生成全部三种报告。

        Returns:
            {filename: content} 生成的报告文件映射
        """
        outputs: dict[str, str] = {}

        md = self.generate_summary_md()
        md_path = self.reports_dir / "summary.md"
        md_path.write_text(md, encoding="utf-8")
        outputs["summary.md"] = str(md_path)

        cj = self.generate_comparison_json()
        cj_path = self.reports_dir / "comparison.json"
        cj_path.write_text(cj, encoding="utf-8")
        outputs["comparison.json"] = str(cj_path)

        csv_content = self.generate_per_query_csv()
        csv_path = self.reports_dir / "per_query.csv"
        csv_path.write_text(csv_content, encoding="utf-8-sig")
        outputs["per_query.csv"] = str(csv_path)

        logger.info("报告已生成: %s", self.reports_dir)
        return outputs

    # ── Markdown 报告 ──────────────────────────────────────────────

    def generate_summary_md(self) -> str:
        """生成 Markdown 对比报告。

        Returns:
            Markdown 格式的报告字符串
        """
        lines = [
            f"# 实验报告: {self.config.name}",
            "",
            f"**描述**: {self.config.description or '(无)'}",
            "",
            f"**数据集**: `{self.config.dataset.queries_path}` / `{self.config.dataset.qrels_path}`",
            f"**索引**: `{self.config.index.path}` (collection={self.config.index.db_name})",
            f"**指标 K 值**: {self.config.metrics.ks}",
            "",
            "---",
            "",
        ]

        # 各 Run 的 pipeline 概览
        lines.append("## Run 配置对比")
        lines.append("")
        lines.append("| Run | Recall | Router | Rerank | Queries |")
        lines.append("|-----|--------|--------|--------|---------|")
        for run_name, result in self.results.items():
            p = result.get("pipeline", {})
            recall = p.get("recall", {})
            router = p.get("router", {})
            rerank = p.get("rerank", {})
            recall_str = f"{recall.get('mode', '?')}(top_k={recall.get('top_k', '?')})"
            router_str = "✓" if router.get("enabled") else "—"
            rerank_str = f"✓(top_k={rerank.get('top_k', '?')})" if rerank.get("enabled") else "—"
            nq = result.get("num_queries", "?")
            lines.append(
                f"| **{run_name}** | {recall_str} | {router_str} | {rerank_str} | {nq} |"
            )
        lines.append("")

        # 聚合指标对比表
        lines.append("## 聚合指标对比")
        lines.append("")
        self._append_metric_table(lines)

        # 成对 Delta 表（如有 ≥2 Run）
        if len(self.results) >= 2:
            lines.append("")
            lines.append("## 成对 Delta")
            lines.append("")
            self._append_delta_table(lines)

        # 分组统计（如有）
        if self.config.metrics.group_by:
            lines.append("")
            lines.append("## 分组统计")
            lines.append("")
            for dim in self.config.metrics.group_by:
                self._append_group_table(lines, dim)

        return "\n".join(lines)

    def _append_metric_table(self, lines: list[str]) -> None:
        """追加聚合指标 Markdown 表格。"""
        metric_keys = self._get_metric_keys()
        if not metric_keys:
            lines.append("(无数据)")
            return

        # 表头
        header = ["Metric"] + list(self.results.keys())
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")

        for mk in metric_keys:
            row = [mk]
            for run_name in self.results:
                summary = self.results[run_name].get("summary", {})
                entry = summary.get(mk, {})
                mean_val = entry.get("mean", float("nan"))
                row.append(f"{mean_val:.4f}")
            lines.append("| " + " | ".join(row) + " |")

    def _append_delta_table(self, lines: list[str]) -> None:
        """追加成对 Delta 表格（每对 Run 的指标差值）。"""
        metric_keys = self._get_metric_keys()
        if not metric_keys:
            return

        run_names = list(self.results.keys())

        lines.append("")
        lines.append("### Delta (baseline vs other)")
        lines.append("")

        baseline = run_names[0]
        for other in run_names[1:]:
            lines.append(f"**{baseline} → {other}**")
            lines.append("")
            header = ["Metric", baseline, other, "Δ"]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "|".join(["---"] * 4) + "|")

            for mk in metric_keys:
                base_val = (
                    self.results[baseline]
                    .get("summary", {})
                    .get(mk, {})
                    .get("mean", float("nan"))
                )
                other_val = (
                    self.results[other]
                    .get("summary", {})
                    .get(mk, {})
                    .get("mean", float("nan"))
                )
                delta = other_val - base_val
                sign = "+" if delta > 0 else ""
                lines.append(
                    f"| {mk} | {base_val:.4f} | {other_val:.4f} | "
                    f"{sign}{delta:.4f} |"
                )
            lines.append("")

    def _append_group_table(self, lines: list[str], dim: str) -> None:
        """追加按维度分组的统计表格。"""
        lines.append(f"### 按 `{dim}` 分组")
        lines.append("")

        # 收集所有 group 值
        all_groups: set[str] = set()
        for result in self.results.values():
            by_group = result.get("by_group", {})
            for g in by_group.get(dim, {}):
                all_groups.add(g)

        if not all_groups:
            lines.append("(无分组数据)")
            return

        metric_keys = self._get_metric_keys()
        if not metric_keys:
            return

        for group_val in sorted(all_groups):
            lines.append(f"**{dim} = {group_val}**")
            lines.append("")
            header = ["Metric"] + list(self.results.keys())
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "|".join(["---"] * len(header)) + "|")

            for mk in metric_keys:
                row = [mk]
                for run_name in self.results:
                    by_group = self.results[run_name].get("by_group", {})
                    dim_data = by_group.get(dim, {})
                    group_summary = dim_data.get(group_val, {})
                    entry = group_summary.get(mk, {})
                    mean_val = entry.get("mean", float("nan"))
                    row.append(f"{mean_val:.4f}")
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

    # ── JSON 对比数据 ─────────────────────────────────────────────

    def generate_comparison_json(self) -> str:
        """生成结构化对比 JSON 数据。

        Returns:
            JSON 字符串
        """
        comparison = {
            "experiment": self.config.name,
            "description": self.config.description,
            "dataset": {
                "queries": self.config.dataset.queries_path,
                "qrels": self.config.dataset.qrels_path,
            },
            "index": {
                "path": self.config.index.path,
                "db_name": self.config.index.db_name,
            },
            "runs": [],
            "deltas": [],
        }

        run_names = list(self.results.keys())
        for run_name in run_names:
            result = self.results[run_name]
            comparison["runs"].append({
                "name": run_name,
                "pipeline": result.get("pipeline", {}),
                "num_queries": result.get("num_queries", 0),
                "summary": result.get("summary", {}),
                "by_group": result.get("by_group", {}),
            })

        # 成对 delta
        if len(run_names) >= 2:
            metric_keys = self._get_metric_keys()
            baseline = run_names[0]
            baseline_summary = self.results[baseline].get("summary", {})
            for other in run_names[1:]:
                other_summary = self.results[other].get("summary", {})
                deltas = {}
                for mk in metric_keys:
                    b = baseline_summary.get(mk, {}).get("mean", 0.0)
                    o = other_summary.get(mk, {}).get("mean", 0.0)
                    deltas[mk] = round(o - b, 6)
                comparison["deltas"].append({
                    "baseline": baseline,
                    "other": other,
                    "deltas": deltas,
                })

        return json.dumps(comparison, ensure_ascii=False, indent=2)

    # ── CSV 明细 ──────────────────────────────────────────────────

    def generate_per_query_csv(self) -> str:
        """生成逐 query 的 CSV 明细。

        Returns:
            CSV 格式字符串
        """
        all_records: list[dict] = []
        for run_name, result in self.results.items():
            for pq in result.get("per_query", []):
                record = {
                    "run": run_name,
                    "query_id": pq.get("query_id", ""),
                    "query": pq.get("query", ""),
                    "query_type": ";".join(pq.get("query_type", [])),
                    "difficulty": pq.get("difficulty", ""),
                    "num_relevant": pq.get("num_relevant", 0),
                    "num_retrieved": pq.get("num_retrieved", 0),
                }
                # 添加所有指标值
                for k, v in pq.items():
                    if isinstance(v, (int, float)):
                        record[k] = v
                all_records.append(record)

        if not all_records:
            return ""

        # 收集所有列名
        fieldnames = list(all_records[0].keys())
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)
        return output.getvalue()

    # ── ASCII 表格 ────────────────────────────────────────────────

    def print_ascii_table(
        self,
        metric: Optional[str] = None,
        group_by: Optional[str] = None,
    ) -> str:
        """生成终端 ASCII 表格。

        Args:
            metric: 指定显示单个指标（None = 显示 Recall@k + MRR + NDCG@10）
            group_by: 分组维度（None = 显示整体聚合）

        Returns:
            格式化的 ASCII 表格字符串
        """
        lines = []
        if not self.results:
            return "无数据"

        run_names = list(self.results.keys())

        if group_by:
            return self._print_grouped_ascii(run_names, group_by, metric)

        # 整体聚合表
        if metric:
            metrics_to_show = [metric]
        else:
            metrics_to_show = self._get_default_display_metrics()

        lines.append(f"Experiment: {self.config.name}")
        lines.append("")

        # 表头
        header = ["Metric"] + run_names
        col_widths = [max(len(h), 12) for h in header]
        lines.append(self._format_row(header, col_widths))
        lines.append(self._format_sep(col_widths))

        for mk in metrics_to_show:
            row = [mk]
            for rn in run_names:
                entry = (
                    self.results[rn]
                    .get("summary", {})
                    .get(mk, {})
                    .get("mean", float("nan"))
                )
                row.append(f"{entry:.4f}")
            lines.append(self._format_row(row, col_widths))

        return "\n".join(lines)

    def _print_grouped_ascii(
        self,
        run_names: list[str],
        group_by: str,
        metric: Optional[str] = None,
    ) -> str:
        """打印分组 ASCII 表格。"""
        # 收集所有 group 值
        all_groups: set[str] = set()
        for result in self.results.values():
            by_group = result.get("by_group", {})
            for g in by_group.get(group_by, {}):
                all_groups.add(g)

        if not all_groups:
            return "无分组数据"

        if metric:
            metrics_to_show = [metric]
        else:
            metrics_to_show = self._get_default_display_metrics()

        lines = [f"Experiment: {self.config.name} (group by {group_by})"]
        lines.append("")

        for group_val in sorted(all_groups):
            lines.append(f"[{group_by} = {group_val}]")
            header = ["Metric"] + run_names
            col_widths = [max(len(h), 12) for h in header]
            lines.append(self._format_row(header, col_widths))
            lines.append(self._format_sep(col_widths))

            for mk in metrics_to_show:
                row = [mk]
                for rn in run_names:
                    by_group = self.results[rn].get("by_group", {})
                    dim_data = by_group.get(group_by, {})
                    group_summary = dim_data.get(group_val, {})
                    entry = group_summary.get(mk, {})
                    mean_val = entry.get("mean", float("nan"))
                    row.append(f"{mean_val:.4f}")
                lines.append(self._format_row(row, col_widths))
            lines.append("")

        return "\n".join(lines)

    # ── 工具方法 ──────────────────────────────────────────────────

    def _get_metric_keys(self) -> list[str]:
        """从结果中提取所有指标 key（保持稳定顺序）。"""
        for result in self.results.values():
            summary = result.get("summary", {})
            if summary:
                return list(summary.keys())
        return []

    def _get_default_display_metrics(self) -> list[str]:
        """返回默认显示的指标关键词列表。"""
        ks = self.config.metrics.ks
        keys = []
        for k in ks:
            keys.append(f"Recall@{k}")
        keys.extend(["MRR", f"NDCG@{ks[-1] if ks else 10}"])
        avail = self._get_metric_keys()
        return [k for k in keys if k in avail]

    @staticmethod
    def _format_row(cells: list[str], widths: list[int]) -> str:
        """格式化表格行。"""
        padded = [c.ljust(w) for c, w in zip(cells, widths)]
        return "| " + " | ".join(padded) + " |"

    @staticmethod
    def _format_sep(widths: list[int]) -> str:
        """格式化分隔线。"""
        parts = ["-" * w for w in widths]
        return "|-" + "-|-".join(parts) + "-|"
