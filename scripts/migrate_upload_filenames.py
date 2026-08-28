"""迁移历史上传文件名：把 data/uploads/ 下纯 uuid 文件名重命名为 uuid__原文件名。

原文件名从 Qdrant 中该文档 chunk 的 metadata.law_name 恢复。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.db import get_engine
import sqlalchemy as sa


def main():
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT doc_id, file_path FROM user_docs")).fetchall()
    engine.dispose()

    if not rows:
        print("没有用户文档，无需迁移")
        return

    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    store = svc._get_store()

    migrated = 0
    for doc_id, file_path in rows:
        p = Path(file_path)
        if not p.exists():
            print(f"[跳过] 文件不存在: {p}")
            continue
        if "__" in p.name:
            print(f"[已迁移] {p.name}")
            continue
        # 从 Qdrant 找原文件名（metadata.law_name）
        original_name = None
        chunks, _ = store.scroll_paginated(
            filter_condition={"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
            limit=1,
        )
        if chunks:
            meta = chunks[0].metadata or {}
            original_name = meta.get("law_name") or chunks[0].doc_id
        if not original_name:
            print(f"[跳过] 无法恢复原文件名: {doc_id}")
            continue
        safe_name = Path(original_name).name
        new_path = p.parent / f"{p.stem}__{safe_name}"
        p.rename(new_path)
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE user_docs SET file_path=:f WHERE doc_id=:d"), {"f": str(new_path), "d": doc_id})
        engine.dispose()
        print(f"[迁移] {p.name} -> {new_path.name}")
        migrated += 1

    print(f"迁移完成：{migrated} 个文件")


if __name__ == "__main__":
    main()
