"""TopK 实验跨方法分析工具。

加载多个 topk-* 实验的结果 JSON，产出：
A. 检索方法 × K 指标矩阵
B. Elbow 分析（边际收益 / 推荐截断点）
C. 分组性能对比（query_type / difficulty）
D. Latency 拆解（mean/P50/P90/P95/P99）
E. 逐 Query 配对对比（加分/掉分分析）

用法:
    python -m evaluation.analyze_topk --exps topk-dense-bge-base,topk-hybrid-bge-base
    python -m evaluation.analyze_topk --exps ... --pairwise A,B --metric Recall@20
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Optional

# ── 自定义类型 ─────────────────────────────────────────────────────
# Results 结构: {exp_name: {run_name: result_dict}}
Results = dict[str, dict[str, dict]]
PairwiseResult = dict[str, Any]

# ── 已知实验 → 显示名映射 ───────────────────────────────────────────
DISPLAY_NAMES = {
    "topk-dense-bge-base": "Dense(bge-base)",
    "topk-dense-bge-m3": "Dense(bge-M3)",
    "topk-dense-qwen": "Dense(Qwen-0.6b)",
    "topk-bm25": "BM25",
    "topk-hybrid-bge-base": "Hybrid(bge-base)",
    "topk-hybrid-router": "Hybrid+Router",
}
ALL_TOPK_EXPS = list(DISPLAY_NAMES.keys())


# ══════════════════════════════════════════════════════════════════
# 4.1 — 结果加载器
# ══════════════════════════════════════════════════════════════════


def load_results(exp_names: list[str]) -> Results:
    """从多个实验目录加载结果 JSON。

    对每个 exp_name，查找 experiments/<exp_name>/results/*.json，
    取第一个（每个实验只有一个 Run）。
    """
    results: Results = {}
    base = Path("experiments")

    for name in exp_names:
        results_dir = base / name / "results"
        if not results_dir.exists():
            print(f"  [WARN] 未找到结果目录: {results_dir}", file=sys.stderr)
            continue

        json_files = sorted(results_dir.glob("*.json"))
        if not json_files:
            print(f"  [WARN] 无结果 JSON 文件: {results_dir}", file=sys.stderr)
            continue

        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        run_name = data.get("run_name", json_files[0].stem)
        results[name] = {run_name: data}

    return results


def _display_name(exp_name: str) -> str:
    """实验名 → 人类可读的短名称。"""
    return DISPLAY_NAMES.get(exp_name, exp_name)


def _get_top_k(results: Results) -> list[int]:
    """从第一个结果中提取 ks 列表。"""
    for exp_data in results.values():
        for run_data in exp_data.values():
            pipeline = run_data.get("pipeline", {})
            recall = pipeline.get("recall", {})
            top_k = recall.get("top_k", 50)
            # 从 summary 中推断实际的 K 值
            summary = run_data.get("summary", {})
            ks = sorted({
                int(k.split("@")[1])
                for k in summary if "@" in k
            })
            if ks:
                return ks
            return [top_k]
    return [10, 20, 30, 40, 50]


# ══════════════════════════════════════════════════════════════════
# A — 检索方法 × K 指标矩阵
# ══════════════════════════════════════════════════════════════════


def build_metric_matrix(results: Results) -> dict[str, dict[str, dict[int, float]]]:
    """构建 {metric: {exp_name: {k: value}}} 矩阵。

    从 summary 中提取每个指标在每种方法、各 K 下的 mean 值。
    """
    matrix: dict[str, dict[str, dict[int, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    for exp_name, exp_data in results.items():
        display = _display_name(exp_name)
        for run_data in exp_data.values():
            summary = run_data.get("summary", {})

            for metric_key, stat_dict in summary.items():
                # 跳过非指标字段（如 timing 相关或维度统计）
                if not isinstance(stat_dict, dict) or "mean" not in stat_dict:
                    continue

                # 提取 K（如果有）
                if "@" in metric_key:
                    base, k_str = metric_key.split("@")
                    k = int(k_str)
                    matrix[metric_key][display][k] = stat_dict["mean"]
                else:
                    # 非 K 指标：AP, MRR
                    matrix[metric_key][display][0] = stat_dict["mean"]

    return dict(matrix)


def print_metric_matrix(matrix: dict, top_ks: list[int]) -> None:
    """打印指标矩阵表格。

    对每个指标输出一张表：行=方法, 列=K, 值=mean。
    每行的最佳值高亮（*标记）。
    """
    # 只显示关注的指标
    target_metrics = [
        f"Recall@{k}" for k in top_ks
    ] + [
        f"Precision@{k}" for k in top_ks
    ] + [
        f"macro_F1@{k}" for k in top_ks
    ] + [
        f"micro_F1@{k}" for k in top_ks
    ] + [
        f"NDCG@{k}" for k in top_ks
    ] + ["AP", "MRR"]

    for metric_key in target_metrics:
        if metric_key not in matrix:
            continue

        data = matrix[metric_key]
        methods = sorted(data.keys(), key=_method_sort_key)

        # 表头
        ks = sorted(
            {k for row in data.values() for k in row if k > 0}
        )

        print(f"\n{'=' * 70}")
        print(f"  {metric_key}")
        print(f"{'=' * 70}")

        header = f"  {'Method':<20s}" + "".join(f"{k:>8d}" for k in ks)
        print(header)
        print(f"  {'-'*20}{'-'*8*len(ks)}")

        for method in methods:
            row = data.get(method, {})
            vals = [f"{row.get(k, float('nan')):>8.4f}" for k in ks]
            best_val = max(row.values()) if row else 0
            # 标记最佳
            marked = []
            for k in ks:
                v = row.get(k, float("nan"))
                if v == best_val and len(row) > 1:
                    marked.append(f"{v:>8.4f}*")
                else:
                    marked.append(f"{v:>8.4f}")
            print(f"  {method:<20s}" + "".join(marked))
        print()


def _method_sort_key(method: str) -> tuple:
    """方法名排序：Dense → Hybrid → BM25 → Router。"""
    order = {"Dense": 0, "Hybrid": 1, "BM25": 2, "Router": 3}
    for prefix, rank in order.items():
        if method.startswith(prefix):
            return (rank, method)
    return (9, method)


# ══════════════════════════════════════════════════════════════════
# B — Elbow 分析
# ══════════════════════════════════════════════════════════════════


def elbow_analysis(results: Results, metric_prefix: str = "macro_F1@") -> dict:
    """分析每种方法的边际收益，推荐 elbow 点。

    marginal_gain(K) = (metric@K - metric@K-10) / 10

    返回 {method: {ks: [...], values: [...], gains: [...], elbow: K}}
    """
    matrix = build_metric_matrix(results)
    top_ks = _get_top_k(results)

    analysis: dict = {}

    for exp_name, exp_data in results.items():
        display = _display_name(exp_name)
        for run_data in exp_data.values():
            summary = run_data.get("summary", {})

            # 提取指定前缀的指标值
            values = {}
            for k in top_ks:
                key = f"{metric_prefix}{k}"
                if key in summary and "mean" in summary[key]:
                    values[k] = summary[key]["mean"]

            if len(values) < 2:
                continue

            sorted_ks = sorted(values.keys())
            sorted_vals = [values[k] for k in sorted_ks]

            # 边际增益
            gains = {}
            for i in range(1, len(sorted_ks)):
                delta_k = sorted_ks[i] - sorted_ks[i-1]
                gain = (sorted_vals[i] - sorted_vals[i-1]) / delta_k
                gains[sorted_ks[i]] = gain

            # Elbow 检测：边际增益首次下降超过 30% 的点
            elbow = None
            if len(gains) >= 2:
                gain_list = sorted(gains.items())
                for i in range(1, len(gain_list)):
                    prev_gain = gain_list[i-1][1]
                    curr_gain = gain_list[i][1]
                    if prev_gain > 0 and curr_gain / prev_gain < 0.7:
                        elbow = gain_list[i-1][0]  # 之前的 K 是 elbow
                        break

            analysis[display] = {
                "ks": sorted_ks,
                "values": {k: round(v, 4) for k, v in values.items()},
                "gains": {k: round(g, 6) for k, g in gains.items()},
                "elbow": elbow,
            }

    return analysis


def print_elbow(analysis: dict, metric_prefix: str = "macro_F1@") -> None:
    """打印 Elbow 分析表格。"""
    print(f"\n{'=' * 70}")
    print(f"  Elbow 分析 — {metric_prefix}K 边际收益")
    print(f"{'=' * 70}")

    for method, data in sorted(analysis.items(), key=lambda x: _method_sort_key(x[0])):
        ks = data["ks"]
        values = data["values"]
        gains = data["gains"]
        elbow = data["elbow"]

        print(f"\n  [{method}]")
        print(f"  {'K':>6s}  {'Value':>8s}  {'Marginal Gain':>14s}  {'Δ%':>8s}")
        print(f"  {'-'*6}  {'-'*8}  {'-'*14}  {'-'*8}")

        for i, k in enumerate(ks):
            val = values.get(k, float("nan"))
            gain = gains.get(k, None)
            if i == 0:
                gain_str = "—"
                pct_str = "—"
            else:
                gain_str = f"{gain:+.6f}" if gain is not None else "—"
                prev_val = values.get(ks[i-1], 0)
                if prev_val > 0:
                    pct = (val - prev_val) / prev_val * 100
                    pct_str = f"{pct:+.1f}%"
                else:
                    pct_str = "—"

            marker = " ← elbow" if k == elbow else ""
            print(f"  {k:>6d}  {val:>8.4f}  {gain_str:>14s}  {pct_str:>8s}{marker}")

    print()


# ══════════════════════════════════════════════════════════════════
# C — 分组性能对比
# ══════════════════════════════════════════════════════════════════


def group_comparison(results: Results, dim: str = "query_type") -> dict:
    """按维度分组对比各方法。

    利用结果中的 by_group.{dim} 字段。
    返回 {group_value: {method: {metric: mean}}}
    """
    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    for exp_name, exp_data in results.items():
        display = _display_name(exp_name)
        for run_data in exp_data.values():
            by_group = run_data.get("by_group", {})
            dim_data = by_group.get(dim, {})

            for group_val, group_summary in dim_data.items():
                for metric_key, stat_dict in group_summary.items():
                    if isinstance(stat_dict, dict) and "mean" in stat_dict:
                        grouped[group_val][display][metric_key] = stat_dict["mean"]
                    elif isinstance(stat_dict, (int, float)):
                        # 标量值（如 count）直接保留
                        grouped[group_val][display][metric_key] = stat_dict

    return dict(grouped)


def print_group_comparison(grouped: dict, dim: str, top_ks: list[int]) -> None:
    """打印分组对比表格。"""
    main_metrics = [f"Recall@{k}" for k in top_ks[:3]] + ["MRR", f"NDCG@{top_ks[-1]}"]

    print(f"\n{'=' * 70}")
    print(f"  按 {dim} 分组对比")
    print(f"{'=' * 70}")

    for group_val in sorted(grouped.keys()):
        methods_data = grouped[group_val]
        methods = sorted(methods_data.keys(), key=_method_sort_key)
        # 从分组数据中提取 query 数量（任意 method 的 count 字段）
        count = 0
        for row in methods_data.values():
            c = row.get("count", 0)
            if isinstance(c, (int, float)) and c > count:
                count = int(c)

        print(f"\n  [{dim} = {group_val}]  (count={count})")

        for metric in main_metrics:
            vals = []
            for method in methods:
                v = methods_data[method].get(metric, float("nan"))
                vals.append((method, v))

            if any(v != float("nan") for _, v in vals):
                best = max(v for _, v in vals if v != float("nan"))
                row = f"    {metric:<16s}"
                for method, v in vals:
                    mark = "*" if v == best and len(vals) > 1 else " "
                    row += f"  {method:<20s}{v:>8.4f}{mark}"
                print(row)


# ══════════════════════════════════════════════════════════════════
# D — Latency 拆解
# ══════════════════════════════════════════════════════════════════


def latency_breakdown(results: Results) -> dict[str, dict[str, dict[str, float]]]:
    """提取各方法的 latency 统计。

    返回 {method: {field: {stat: value}}}
    """
    breakdown: dict = {}

    for exp_name, exp_data in results.items():
        display = _display_name(exp_name)
        for run_data in exp_data.values():
            summary = run_data.get("summary", {})

            latency_stats = {}
            for key, stat_dict in summary.items():
                if key.startswith("latency_") and isinstance(stat_dict, dict):
                    field = key.replace("latency_", "")
                    latency_stats[field] = stat_dict

            if latency_stats:
                breakdown[display] = latency_stats

    return breakdown


def print_latency(breakdown: dict) -> None:
    """打印延迟拆解表格。"""
    stages = ["prefilter", "router", "recall", "rerank", "total"]
    stats = ["mean", "p50", "p90", "p95", "p99"]
    # 只显示存在的方法
    methods = sorted(breakdown.keys(), key=_method_sort_key)

    if not methods:
        print("\n  无延迟数据")
        return

    print(f"\n{'=' * 70}")
    print(f"  Latency 拆解（单位：秒）")
    print(f"{'=' * 70}")

    for stage in stages:
        print(f"\n  --- {stage} ---")
        header = f"  {'Stat':<8s}" + "".join(f"{m:>14s}" for m in methods)
        print(header)
        print(f"  {'-'*8}" + "".join(f"{'-'*14}" for _ in methods))

        for stat in stats:
            row = f"  {stat:<8s}"
            for method in methods:
                stage_data = breakdown[method].get(stage, {})
                val = stage_data.get(stat, float("nan"))
                if val == float("nan"):
                    row += f"{'—':>14s}"
                elif val >= 1:
                    row += f"{val:>14.3f}"
                elif val >= 0.001:
                    row += f"{val*1000:>10.2f}ms"
                else:
                    row += f"{val*1000*1000:>8.0f}µs"
            print(row)
    print()


# ══════════════════════════════════════════════════════════════════
# E — 逐 Query 配对对比 ⭐
# ══════════════════════════════════════════════════════════════════


def paired_comparison(
    results_a: dict, results_b: dict,
    metric: str = "Recall@20",
) -> PairwiseResult:
    """对两个方法的逐 query 指标进行配对对比。

    delta = method_A - method_B

    Args:
        results_a, results_b: 从 single_run_results() 返回的 {run_name: dict}
        metric: 要对比的指标名（如 "Recall@20", "NDCG@10", "MRR"）

    Returns:
        dict 包含总体统计、加分/掉分 query 列表
    """
    # 取第一个 run 的 per_query 数据
    run_a = next(iter(results_a.values()))
    run_b = next(iter(results_b.values()))
    pq_a: list[dict] = run_a.get("per_query", [])
    pq_b: list[dict] = run_b.get("per_query", [])

    if not pq_a or not pq_b:
        return {"error": "无 per_query 数据"}

    # 构建 query_id → metric 映射
    map_a = {q["query_id"]: q for q in pq_a}
    map_b = {q["query_id"]: q for q in pq_b}

    # 对齐 query
    common_ids = sorted(set(map_a.keys()) & set(map_b.keys()))

    deltas: list[dict] = []
    for qid in common_ids:
        val_a = map_a[qid].get(metric, float("nan"))
        val_b = map_b[qid].get(metric, float("nan"))
        if val_a == float("nan") or val_b == float("nan"):
            continue

        delta = val_a - val_b
        deltas.append({
            "query_id": qid,
            "query": map_a[qid].get("query", ""),
            "query_type": map_a[qid].get("query_type", []),
            "difficulty": map_a[qid].get("difficulty", ""),
            "val_a": val_a,
            "val_b": val_b,
            "delta": delta,
            "abs_delta": abs(delta),
        })

    if not deltas:
        return {"error": "无对齐 query"}

    deltas.sort(key=lambda x: x["delta"], reverse=True)

    # 总体统计
    all_deltas = [d["delta"] for d in deltas]
    n = len(all_deltas)
    improved = sum(1 for d in all_deltas if d > 1e-6)
    degraded = sum(1 for d in all_deltas if d < -1e-6)
    unchanged = n - improved - degraded
    sorted_deltas = sorted(all_deltas)

    # 加分/掉分布
    add_dist = Counter()
    dec_dist = Counter()
    for d in deltas:
        qt = ";".join(d["query_type"]) if isinstance(d["query_type"], list) else str(d["query_type"])
        diff = d["difficulty"]
        if d["delta"] > 1e-6:
            add_dist[qt] += 1
        elif d["delta"] < -1e-6:
            dec_dist[qt] += 1

    result: PairwiseResult = {
        "metric": metric,
        "num_aligned": n,
        "mean_delta": mean(all_deltas),
        "std_delta": stdev(all_deltas) if len(all_deltas) > 1 else 0.0,
        "min_delta": sorted_deltas[0],
        "max_delta": sorted_deltas[-1],
        "p50_delta": sorted_deltas[n // 2],
        "p95_delta": sorted_deltas[min(int(n * 0.95), n - 1)],
        "p99_delta": sorted_deltas[min(int(n * 0.99), n - 1)],
        "improved": improved,
        "degraded": degraded,
        "unchanged": unchanged,
        "improved_pct": improved / n * 100,
        "degraded_pct": degraded / n * 100,
        "unchanged_pct": unchanged / n * 100,
        "top_gainers": deltas[:50],
        "top_decliners": list(reversed(deltas[-50:])),
        "improve_by_query_type": dict(add_dist.most_common()),
        "degrade_by_query_type": dict(dec_dist.most_common()),
    }

    return result


def print_paired(pair: PairwiseResult, name_a: str, name_b: str, top_k: int = 10) -> None:
    """打印配对对比结果。"""
    if "error" in pair:
        print(f"\n  ⚠ 配对对比出错: {pair['error']}")
        return

    print(f"\n{'=' * 70}")
    print(f"  逐 Query 配对对比: {name_a} vs {name_b}")
    print(f"  指标: {pair['metric']}")
    print(f"{'=' * 70}")

    # 总体统计
    print(f"\n  总体统计:")
    print(f"    对齐 query: {pair['num_aligned']}")
    print(f"    Mean Delta: {pair['mean_delta']:+.6f}")
    print(f"    Std  Delta: {pair['std_delta']:.6f}")
    print(f"    Min / Max:  {pair['min_delta']:+.6f} / {pair['max_delta']:+.6f}")
    print(f"    P50 / P95 / P99: {pair['p50_delta']:+.6f} / {pair['p95_delta']:+.6f} / {pair['p99_delta']:+.6f}")
    print(f"    ↑ 改善: {pair['improved']} ({pair['improved_pct']:.1f}%)")
    print(f"    ↓ 退步: {pair['degraded']} ({pair['degraded_pct']:.1f}%)")
    print(f"    — 不变: {pair['unchanged']} ({pair['unchanged_pct']:.1f}%)")

    # Top 加分
    print(f"\n  Top-{top_k} 加分 query（{name_a} 明显优于 {name_b}）:")
    print(f"  {'Query ID':<20s}  {'Delta':>8s}  {'A':>8s}  {'B':>8s}  {'Difficulty':<12s}  {'Type'}")
    print(f"  {'-'*20}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*12}  {'-'*30}")
    for d in pair["top_gainers"][:top_k]:
        qt = ";".join(d["query_type"]) if isinstance(d["query_type"], list) else str(d["query_type"])
        print(f"  {d['query_id']:<20s}  {d['delta']:>+8.4f}  {d['val_a']:>8.4f}  {d['val_b']:>8.4f}  {d['difficulty']:<12s}  {qt:<30s}")

    # Top 掉分
    print(f"\n  Top-{top_k} 掉分 query（{name_a} 明显劣于 {name_b}）:")
    print(f"  {'Query ID':<20s}  {'Delta':>8s}  {'A':>8s}  {'B':>8s}  {'Difficulty':<12s}  {'Type'}")
    print(f"  {'-'*20}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*12}  {'-'*30}")
    for d in pair["top_decliners"][:top_k]:
        qt = ";".join(d["query_type"]) if isinstance(d["query_type"], list) else str(d["query_type"])
        print(f"  {d['query_id']:<20s}  {d['delta']:>+8.4f}  {d['val_a']:>8.4f}  {d['val_b']:>8.4f}  {d['difficulty']:<12s}  {qt:<30s}")

    # 加分 query 类型分布
    print(f"\n  加分 query 类型分布:")
    for qt, cnt in pair.get("improve_by_query_type", {}).items():
        print(f"    {qt}: {cnt}")

    print(f"\n  掉分 query 类型分布:")
    for qt, cnt in pair.get("degrade_by_query_type", {}).items():
        print(f"    {qt}: {cnt}")

    print()


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════


def run_analysis(
    exp_names: list[str] | None = None,
    metric: str | None = None,
    group_by: str | None = None,
    pairwise: list[tuple[str, str]] | None = None,
    elbow: bool = False,
    latency: bool = False,
    json_output: bool = False,
) -> dict | None:
    """执行分析并输出。

    Args:
        exp_names: 实验名列表（None = 全部 topk 实验）
        metric: 指定单个指标
        group_by: 分组维度
        pairwise: 配对对比列表，如 [("A","B")]
        elbow: 是否执行 elbow 分析
        latency: 是否输出延迟分析
        json_output: 是否输出 JSON

    Returns:
        json_output=True 时返回结构化 dict
    """
    if exp_names is None:
        exp_names = ALL_TOPK_EXPS

    results = load_results(exp_names)
    if not results:
        print("错误: 未加载任何结果。请先运行实验。")
        return None

    top_ks = _get_top_k(results)
    matrix = build_metric_matrix(results)

    output: dict = {}

    # A — 矩阵
    if json_output:
        output["metric_matrix"] = matrix
    else:
        print_metric_matrix(matrix, top_ks)

    # B — Elbow
    if elbow:
        all_elbow = {}
        for prefix in ["Recall@", "NDCG@", "macro_F1@"]:
            analysis = elbow_analysis(results, metric_prefix=prefix)
            if analysis:
                all_elbow[prefix] = analysis
        if json_output:
            output["elbow"] = all_elbow
        else:
            for prefix, analysis in all_elbow.items():
                print_elbow(analysis, metric_prefix=prefix)

    # C — 分组
    if group_by:
        grouped = group_comparison(results, group_by)
        if json_output:
            output[f"group_by_{group_by}"] = grouped
        else:
            print_group_comparison(grouped, group_by, top_ks)

    # D — Latency
    if latency:
        lb = latency_breakdown(results)
        if json_output:
            output["latency"] = lb
        else:
            print_latency(lb)

    # E — 配对对比
    if pairwise:
        pairs_output = {}
        for name_a, name_b in pairwise:
            if name_a not in results:
                print(f"  [WARN] 未加载实验: {name_a}")
                continue
            if name_b not in results:
                print(f"  [WARN] 未加载实验: {name_b}")
                continue
            met = metric or "Recall@20"
            pair = paired_comparison(
                results[name_a], results[name_b], met
            )
            if json_output:
                pairs_output[f"{name_a}_vs_{name_b}"] = pair
            else:
                print_paired(pair, _display_name(name_a), _display_name(name_b))

        if json_output:
            output["pairwise"] = pairs_output

    if json_output:
        return output

    return None


def main():
    """CLI 入口（python -m evaluation.analyze_topk）。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="TopK 实验跨方法分析工具"
    )
    parser.add_argument(
        "--exps",
        default=",".join(ALL_TOPK_EXPS),
        help="逗号分隔的实验名列表（默认全部）",
    )
    parser.add_argument(
        "--metric", default=None,
        help="指定指标（如 Recall@20, NDCG@10）",
    )
    parser.add_argument(
        "--by", default=None,
        help="分组维度（query_type / difficulty）",
    )
    parser.add_argument(
        "--pairwise", action="append", default=None,
        help="配对对比，格式 A,B（可多次指定）",
    )
    parser.add_argument(
        "--elbow", action="store_true", default=False,
        help="执行 elbow 分析",
    )
    parser.add_argument(
        "--latency", action="store_true", default=False,
        help="输出延迟分析",
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="输出 JSON 格式",
    )

    args = parser.parse_args()
    exp_names = [e.strip() for e in args.exps.split(",") if e.strip()]

    pairwise_pairs = None
    if args.pairwise:
        pairwise_pairs = []
        for pair_str in args.pairwise:
            parts = [p.strip() for p in pair_str.split(",")]
            if len(parts) == 2:
                pairwise_pairs.append((parts[0], parts[1]))

    result = run_analysis(
        exp_names=exp_names,
        metric=args.metric,
        group_by=args.by,
        pairwise=pairwise_pairs,
        elbow=args.elbow,
        latency=args.latency,
        json_output=args.json,
    )

    if args.json and result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
