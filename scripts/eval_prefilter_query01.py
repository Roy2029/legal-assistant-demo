"""PreFilter 评估脚本（query-01 数据集）。

数据集：data/eval/prefilter_query01.jsonl
标签：1=法律问题（应放行），0=非法律问题（应拦截）。

评估重点：
1. 法律问题误判率（legal blocked rate）：法律问题中被 PreFilter 拦错的比例，越低越好。
2. 非法律问题拦截率（non_legal blocked rate）：非法律问题中被正确拦截的比例。
3. 另附 overall accuracy / confusion matrix / 误判与漏放样本。

用法：
  .venv/Scripts/python scripts/eval_prefilter_query01.py
  .venv/Scripts/python scripts/eval_prefilter_query01.py --show 20   # 最多展示的误判/漏放样本数
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.prefilter import prefilter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "eval" / "prefilter_query01.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--output", default=str(ROOT / "data" / "eval" / "prefilter_query01_report.json"))
    ap.add_argument("--show", type=int, default=20, help="误判/漏放样本展示条数")
    args = ap.parse_args()

    rows = []
    with open(args.dataset, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d.setdefault("source", "unknown")
            rows.append(d)

    legal = [r for r in rows if r.get("label") == 1]
    non_legal = [r for r in rows if r.get("label") == 0]
    print(f"数据集 {args.dataset}")
    print(f"总样本 {len(rows)} | 法律问题 {len(legal)} | 非法律问题 {len(non_legal)}")

    blocked_legal = []   # 误判：法律问题被拦
    passed_legal = []    # 法律问题放行
    blocked_nonlegal = []  # 非法律问题正确拦截
    passed_nonlegal = []   # 漏放：非法律问题被放行

    for r in rows:
        pf = prefilter(r["query"])
        passed = pf["passed"]
        rec = {**r, "passed": passed, "reason": pf.get("reason")}
        if r.get("label") == 1:
            if passed:
                passed_legal.append(rec)
            else:
                blocked_legal.append(rec)
        else:
            if passed:
                passed_nonlegal.append(rec)
            else:
                blocked_nonlegal.append(rec)

    n_legal = len(legal)
    n_nonlegal = len(non_legal)
    n_blocked_legal = len(blocked_legal)
    n_blocked_nonlegal = len(blocked_nonlegal)
    n_passed_nonlegal = len(passed_nonlegal)
    n_passed_legal = len(passed_legal)

    legal_blocked_rate = n_blocked_legal / n_legal if n_legal else 0.0  # 法律问题误判率
    legal_pass_rate = n_passed_legal / n_legal if n_legal else 0.0
    nonlegal_blocked_rate = n_blocked_nonlegal / n_nonlegal if n_nonlegal else 0.0  # 非法律拦截率
    nonlegal_pass_rate = n_passed_nonlegal / n_nonlegal if n_nonlegal else 0.0
    accuracy = (n_passed_legal + n_blocked_nonlegal) / len(rows) if rows else 0.0

    print("\n===== 评估结果 =====")
    print(f"法律问题误判率（应放行被拦）: {legal_blocked_rate:.4f}  ({n_blocked_legal}/{n_legal})")
    print(f"法律问题通过率（应放行放行）: {legal_pass_rate:.4f}  ({n_passed_legal}/{n_legal})")
    print(f"非法律问题拦截率（应拦截被拦）: {nonlegal_blocked_rate:.4f}  ({n_blocked_nonlegal}/{n_nonlegal})")
    print(f"非法律问题漏放率（应拦截被放）: {nonlegal_pass_rate:.4f}  ({n_passed_nonlegal}/{n_nonlegal})")
    print(f"总体准确率: {accuracy:.4f}  ({n_passed_legal + n_blocked_nonlegal}/{len(rows)})")

    # 误判样本（法律问题被拦）——最需要关注
    print(f"\n===== 误判样本（法律问题被拦截，前 {args.show}）=====")
    for rec in blocked_legal[: args.show]:
        print(f"  [{rec['reason']}] {rec['query']}")

    # 漏放样本（非法律问题被放行）
    print(f"\n===== 漏放样本（非法律问题被放行，前 {args.show}）=====")
    for rec in passed_nonlegal[: args.show]:
        print(f"  [{rec['reason'] or 'passed'}] {rec['query']}")

    report = {
        "dataset": args.dataset,
        "total": len(rows),
        "legal_total": n_legal,
        "non_legal_total": n_nonlegal,
        "confusion": {
            "legal_passed": n_passed_legal,
            "legal_blocked": n_blocked_legal,
            "non_legal_blocked": n_blocked_nonlegal,
            "non_legal_passed": n_passed_nonlegal,
        },
        "primary_metric": {
            "legal_blocked_rate": round(legal_blocked_rate, 4),
            "legal_pass_rate": round(legal_pass_rate, 4),
        },
        "secondary_metric": {
            "non_legal_blocked_rate": round(nonlegal_blocked_rate, 4),
            "non_legal_pass_rate": round(nonlegal_pass_rate, 4),
        },
        "overall_accuracy": round(accuracy, 4),
        "blocked_legal": blocked_legal,
        "passed_nonlegal": passed_nonlegal,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {out_path}")


if __name__ == "__main__":
    main()
