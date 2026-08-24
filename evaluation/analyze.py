"""实验结果分析工具。

提供：
1. `join_runs()` — 将两个 Run 的结果按 query_id 对齐，输出 CSV 供 pandas 分析
2. CLI 入口：`python -m evaluation.analyze join <run1.json> <run2.json> -o out.csv`
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


def load_results(path: str | Path) -> dict:
    """加载单个 Run 的结果 JSON。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def join_runs(
    path_a: str | Path,
    path_b: str | Path,
    label_a: str = "run_a",
    label_b: str = "run_b",
    metrics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """将两个 Run 的 per_query 按 query_id 对齐，返回行列表。

    每行包含：
    - query_id / query / query_type / difficulty（共通）
    - <label>.<metric>  / <label_b>.<metric>（各自的指标值）
    - Δ<metric>（差值 = run_b - run_a）

    Args:
        path_a: Run A 的结果文件路径
        path_b: Run B 的结果文件路径
        label_a: Run A 的列名前缀（默认 "run_a"）
        label_b: Run B 的列名前缀（默认 "run_b"）
        metrics: 要对比的指标列表，默认全部 Recall@k / Precision@k / F1@k / MRR / NDCG@k

    Returns:
        dict 列表，每项一个 query，可直接喂给 pandas.DataFrame
    """
    data_a = load_results(path_a)
    data_b = load_results(path_b)

    pq_a = {q["query_id"]: q for q in data_a["per_query"]}
    pq_b = {q["query_id"]: q for q in data_b["per_query"]}

    # 自动发现指标列：取两边的并集，过滤掉元数据
    skip_keys = {"query_id", "query", "query_type", "difficulty",
                  "num_relevant", "num_retrieved"}
    all_metric_keys: set[str] = set()
    for q in data_a["per_query"]:
        all_metric_keys.update(k for k in q if k not in skip_keys)
    for q in data_b["per_query"]:
        all_metric_keys.update(k for k in q if k not in skip_keys)
    all_metrics = sorted(all_metric_keys)

    if metrics:
        all_metrics = [m for m in all_metrics if m in metrics]

    joined: list[dict[str, Any]] = []

    # 取 query_id 并集（两边都可能多出/缺失）
    all_ids = set(pq_a) | set(pq_b)
    for qid in sorted(all_ids):
        qa = pq_a.get(qid)
        qb = pq_b.get(qid)
        if qa is None or qb is None:
            continue  # 跳过只在一侧出现的 query

        row: dict[str, Any] = {
            "query_id": qid,
            "query": qa.get("query", ""),
            "query_type": ",".join(qa.get("query_type", [])),
            "difficulty": qa.get("difficulty", ""),
        }

        for m in all_metrics:
            va = qa.get(m)
            vb = qb.get(m)
            row[f"{label_a}.{m}"] = va
            row[f"{label_b}.{m}"] = vb
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                row[f"Δ{m}"] = vb - va

        joined.append(row)

    return joined


def write_csv(rows: list[dict[str, Any]], output: str | Path) -> None:
    """将对齐结果写出 CSV。"""
    if not rows:
        print("警告: 无对齐数据，未生成 CSV")
        return
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"已写出 {len(rows)} 行 → {output}")


# ── CLI ────────────────────────────────────────────────


def cli_join(args: list[str]) -> None:
    """handles `python -m evaluation.analyze join <a> <b> -o <out>`"""
    import argparse

    parser = argparse.ArgumentParser(description="对齐两个 Run 的 per_query 结果")
    parser.add_argument("run_a", type=str, help="Run A 的结果 JSON")
    parser.add_argument("run_b", type=str, help="Run B 的结果 JSON")
    parser.add_argument("-o", "--output", type=str, default="comparison.csv",
                        help="输出 CSV 路径")
    parser.add_argument("--label-a", type=str, default="run_a")
    parser.add_argument("--label-b", type=str, default="run_b")
    parser.add_argument("--metrics", type=str, default=None,
                        help="要对比的指标，逗号分隔，默认全部")
    parsed = parser.parse_args(args)

    metrics = parsed.metrics.split(",") if parsed.metrics else None
    rows = join_runs(
        parsed.run_a, parsed.run_b,
        label_a=parsed.label_a, label_b=parsed.label_b,
        metrics=metrics,
    )
    write_csv(rows, parsed.output)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m evaluation.analyze <command> [args...]")
        print("命令:")
        print("  join <a.json> <b.json> -o out.csv   对齐两个 Run")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "join":
        cli_join(sys.argv[2:])
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
