"""评估报告自动生成（D03 §4.3）：汇总 PreFilter 评测与核心指标，输出 Markdown。"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_prefilter_eval() -> dict:
    from server.prefilter import prefilter
    evalf = ROOT / "data" / "eval" / "prefilter_eval.jsonl"
    rows = [json.loads(ln) for ln in evalf.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tp = tn = fp = fn = 0
    for r in rows:
        blocked = not prefilter(r["query"])["passed"]
        if blocked and r["should_block"]:
            tp += 1
        elif not blocked and not r["should_block"]:
            tn += 1
        elif blocked:
            fp += 1
        else:
            fn += 1
    return {"total": len(rows), "accuracy": round((tp + tn) / len(rows), 3), "fp": fp, "fn": fn}


def main():
    out_dir = ROOT / "data" / "eval_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    pf = run_prefilter_eval()
    report = f"""# 评估报告 {datetime.now():%Y-%m-%d %H:%M}

## PreFilter 评测
- 总数 {pf['total']} | 准确率 {pf['accuracy']} | 误杀 {pf['fp']} | 漏放 {pf['fn']}

## 说明
- 检索链路 qrels 评估：旧 qrels 因 chunker 变更停用，新 qrels 待用户重建。
- 引用可验证率：依赖 LLM 批量测试，待配置后执行。
"""
    out = out_dir / f"eval_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
