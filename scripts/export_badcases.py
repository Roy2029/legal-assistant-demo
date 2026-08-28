"""导出 badcase 反馈为 JSON/CSV 并打印摘要。

用法:
  .venv/Scripts/python scripts/export_badcases.py
  .venv/Scripts/python scripts/export_badcases.py --status new
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.db import get_engine
import sqlalchemy as sa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default=None, help="按状态过滤 new/pending/fixed/closed")
    ap.add_argument("--out-json", default="data/badcases_export.json")
    ap.add_argument("--out-csv", default="data/badcases_export.csv")
    args = ap.parse_args()

    engine = get_engine()
    sql = "SELECT * FROM badcase_feedback"
    params = {}
    if args.status:
        sql += " WHERE status=:s"
        params["s"] = args.status
    sql += " ORDER BY id DESC"
    with engine.begin() as conn:
        rows = conn.execute(sa.text(sql), params).fetchall()
    engine.dispose()
    data = [dict(r._mapping) for r in rows]

    if not data:
        print("没有 badcase 记录")
        return

    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(args.out_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)

    print(f"导出 {len(data)} 条 → {args.out_json} / {args.out_csv}")
    print("\n按原因:", dict(Counter(r["reason"] for r in data)))
    print("按状态:", dict(Counter(r["status"] for r in data)))
    print("\n最近 5 条:")
    for r in data[:5]:
        print(f"  #{r['id']} [{r['status']}] ({r['reason']}) {r['query'][:60]}")


if __name__ == "__main__":
    main()
