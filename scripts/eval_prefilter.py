"""PreFilter 评测脚本（D02 冒烟集：30 不应拦截 + 10 应拦截）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.prefilter import prefilter

EVAL_FILE = Path(__file__).resolve().parents[1] / "data" / "eval" / "prefilter_eval.jsonl"


def main():
    rows = [json.loads(ln) for ln in EVAL_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tp = tn = fp = fn = 0
    for r in rows:
        pf = prefilter(r["query"])
        blocked = not pf["passed"]
        if blocked and r["should_block"]:
            tp += 1
        elif not blocked and not r["should_block"]:
            tn += 1
        elif blocked and not r["should_block"]:
            fp += 1
            print(f"  [误杀] {r['query']}")
        else:
            fn += 1
            print(f"  [漏放] {r['query']}")
    total = len(rows)
    acc = (tp + tn) / total
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"总数 {total} | TP {tp} TN {tn} FP {fp} FN {fn}")
    print(f"Accuracy {acc:.3f} | Precision {precision:.3f} | Recall {recall:.3f}")
    # M0 验收线：不应拦截样本 100% 放行（FP=0）
    if fp == 0:
        print("PASS: 无误杀")
    else:
        print(f"FAIL: {fp} 条误杀")
        sys.exit(1)


if __name__ == "__main__":
    main()
