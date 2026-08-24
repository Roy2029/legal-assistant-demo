"""检索评测指标：纯计算，无外部依赖。"""

import math
from statistics import mean, stdev
from typing import List, Set, Dict, Any, Optional


def recall_at_k(
    retrieved_ids: List[str], relevant_ids: Set[str], k: int
) -> float:
    """Recall@k: 前 k 个结果中命中的相关文档数 / 总相关文档数。"""
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in relevant_ids)
    return hits / len(relevant_ids)


def precision_at_k(
    retrieved_ids: List[str], relevant_ids: Set[str], k: int
) -> float:
    """Precision@k: 前 k 个结果中命中的相关文档数 / k。"""
    if k == 0:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in relevant_ids)
    return hits / k


def f1_at_k(
    retrieved_ids: List[str], relevant_ids: Set[str], k: int
) -> float:
    """F1@k: Precision@k 和 Recall@k 的调和平均（per-query 宏观 F1）。"""
    p = precision_at_k(retrieved_ids, relevant_ids, k)
    r = recall_at_k(retrieved_ids, relevant_ids, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def micro_f1_at_k(
    all_retrieved_ids: List[List[str]],
    all_relevant_ids: List[Set[str]],
    k: int,
) -> float:
    """Micro-F1@k: 全局聚合 TP/FP/FN 后计算 F1。

    与 per-query F1 取均值（macro-F1）不同，micro-F1 将所有 query
    的 TP/FP 汇总后一次性计算 F1，更反映大规模集合上的全局性能。

    Args:
        all_retrieved_ids: 每个 query 的检索结果 ID 列表。
        all_relevant_ids: 每个 query 的相关 ID 集合。
        k: 截断深度。

    Returns:
        micro-F1@k 值，范围 [0, 1]。
    """
    total_tp = 0
    total_fp = 0
    total_fn = 0

    for retrieved_ids, relevant_ids in zip(all_retrieved_ids, all_relevant_ids):
        if not relevant_ids:
            continue
        top_k = retrieved_ids[:k]
        hits = sum(1 for cid in top_k if cid in relevant_ids)
        total_tp += hits
        total_fp += k - hits
        total_fn += len(relevant_ids) - hits

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def average_precision(
    retrieved_ids: List[str], relevant_ids: Set[str]
) -> float:
    """Average Precision (AP): 每个相关位置的 Precision 的均值。

    这是信息检索领域的标准 AP 定义。
    """
    if not relevant_ids:
        return 0.0
    relevant_set = set(relevant_ids)
    score = 0.0
    hits = 0
    for i, cid in enumerate(retrieved_ids):
        if cid in relevant_set:
            hits += 1
            score += hits / (i + 1)
    return score / len(relevant_ids)


def mean_reciprocal_rank(
    retrieved_ids: List[str], relevant_ids: Set[str]
) -> float:
    """MRR: 第一个相关结果的倒数排名。没有相关结果时返回 0。"""
    for i, cid in enumerate(retrieved_ids):
        if cid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(
    retrieved_ids: List[str],
    relevant_ids: Set[str],
    k: int,
    relevance_grades: Optional[Dict[str, int]] = None,
) -> float:
    """NDCG@k: 归一化折损累计增益，支持分级相关性。

    Args:
        retrieved_ids: 检索结果 ID 列表（按分数降序）。
        relevant_ids: 所有相关 ID 集合。
        k: 截断深度。
        relevance_grades: 可选，{chunk_id: grade} 分级映射，grade 从 0 开始。
                          未提供的 ID 默认为 1（如果在 relevant_ids 中）或 0。

    Returns:
        NDCG@k 值，范围 [0, 1]。
    """
    if k == 0:
        return 0.0

    def _grade(cid: str) -> int:
        if relevance_grades is not None and cid in relevance_grades:
            return relevance_grades[cid]
        return 1 if cid in relevant_ids else 0

    dcg = 0.0
    for i, cid in enumerate(retrieved_ids[:k]):
        rel = _grade(cid)
        if i == 0:
            dcg += rel
        else:
            dcg += rel / math.log2(i + 1)

    # IDCG：按 grade 降序排列的理想排序
    ideal_grades = sorted(
        [_grade(cid) for cid in retrieved_ids[:k]] +
        [_grade(cid) for cid in relevant_ids if cid not in retrieved_ids[:k]],
        reverse=True,
    )[:k]
    idcg = 0.0
    for i, rel in enumerate(ideal_grades):
        if i == 0:
            idcg += rel
        else:
            idcg += rel / math.log2(i + 1)

    return dcg / idcg if idcg > 0 else 0.0


def compute_all_metrics(
    retrieved_ids: List[str],
    relevant_ids: Set[str],
    ks: List[int],
    relevance_grades: Optional[Dict[str, int]] = None,
) -> Dict[str, float]:
    """一次性计算所有支持的指标。

    Returns:
        {metric_name: value} 字典，包含 Recall@k / Precision@k / F1@k / AP / MRR / NDCG@k。
    """
    metrics: Dict[str, float] = {}

    for k in ks:
        metrics[f"Recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
        metrics[f"Precision@{k}"] = precision_at_k(retrieved_ids, relevant_ids, k)
        metrics[f"F1@{k}"] = f1_at_k(retrieved_ids, relevant_ids, k)

    metrics["AP"] = average_precision(retrieved_ids, relevant_ids)
    metrics["MRR"] = mean_reciprocal_rank(retrieved_ids, relevant_ids)

    for k in ks:
        metrics[f"NDCG@{k}"] = ndcg_at_k(
            retrieved_ids, relevant_ids, k, relevance_grades
        )

    return metrics


def compute_metrics_summary(
    all_metrics: List[Dict[str, float]],
) -> Dict[str, Dict[str, float]]:
    """跨所有问题的指标聚合统计。

    Args:
        all_metrics: compute_all_metrics 返回的字典列表，每个问题一个。

    Returns:
        {metric_name: {mean, std, min, max, p50, p90}} 的嵌套字典。
    """
    if not all_metrics:
        return {}

    metric_names = list(all_metrics[0].keys())
    summary: Dict[str, Dict[str, float]] = {}

    for name in metric_names:
        values = [m[name] for m in all_metrics]
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        summary[name] = {
            "mean": mean(values),
            "std": stdev(values) if len(values) > 1 else 0.0,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "p50": sorted_vals[n // 2],
            "p90": sorted_vals[int(n * 0.9)],
            "p95": sorted_vals[min(int(n * 0.95), n - 1)],
            "p99": sorted_vals[min(int(n * 0.99), n - 1)],
        }

    return summary
