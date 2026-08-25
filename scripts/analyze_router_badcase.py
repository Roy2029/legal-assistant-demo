"""Router badcase 分析框架（M2）：读取 hybrid_vs_router.csv 做基础对比与坏例归因。

数据源：RAG1.0 experiments/hybrid_vs_router.csv（2253 query × 70 字段）。
用法：.venv/Scripts/python scripts/analyze_router_badcase.py
"""
import csv
import sys
from pathlib import Path

CSV_PATH = Path("D:/个人/Research/RAG1.0/experiments/hybrid_vs_router.csv")


def main():
    if not CSV_PATH.exists():
        print(f"数据文件不存在: {CSV_PATH}")
        print("跳过 Router badcase 分析（数据在 RAG1.0 仓库，M2 人工阶段执行）")
        return
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"共 {len(rows)} 条 query 对比数据")
    # 打印字段名（前 40 个）供归因参考
    fields = list(rows[0].keys()) if rows else []
    print(f"字段数 {len(fields)}：")
    print("  " + ", ".join(fields[:40]))
    # TODO(M2)：按 D03 §5.3 根因分类做逐条归因，结合 trace 分析 Router 负优化原因
    print("框架就绪：M2 人工/自动归因阶段填充分析逻辑。")


if __name__ == "__main__":
    main()
