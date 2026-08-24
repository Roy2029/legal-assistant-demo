"""SQLite 检索结果缓存。

以 (query_hash, pipeline_config_hash) 为 key 缓存检索结果的 chunk ID 和分数列表。
跨 Run 复用：同一 Experiment 的多个 Run 共享同一数据集，当 recall 配置相同时可复用。

用法:
    cache = RecallCache("experiments/my-exp/cache/recall_cache.db")
    cached = cache.get(query_text, recall_config)   # 命中返回 chunks 列表，否则 None
    cache.put(query_text, recall_config, chunks)     # 写入
    h, m = cache.stats()                             # (hits, misses)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CachedChunk:
    """缓存中存储的简化 chunk 信息。"""

    chunk_id: str
    score: float
    content: str
    doc_id: str


class RecallCache:
    """SQLite 检索缓存。

    数据库位置: experiments/<name>/cache/recall_cache.db
    表: recall_cache
        - cache_key TEXT PRIMARY KEY
        - query_text TEXT
        - pipeline_hash TEXT
        - chunk_ids TEXT (JSON array)
        - scores TEXT (JSON array)
        - contents TEXT (JSON array)
        - doc_ids TEXT (JSON array)
        - created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._hits: int = 0
        self._misses: int = 0
        self._init_db()

    def _init_db(self) -> None:
        """创建缓存表和索引（如不存在）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recall_cache (
                    cache_key TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    pipeline_hash TEXT NOT NULL,
                    chunk_ids TEXT NOT NULL,
                    scores TEXT NOT NULL,
                    contents TEXT NOT NULL DEFAULT '[]',
                    doc_ids TEXT NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pipeline_hash
                ON recall_cache(pipeline_hash)
            """)
            conn.commit()

    # ── 缓存 key 计算 ──────────────────────────────────────────────

    @staticmethod
    def compute_key(query_text: str, recall_config_json: str) -> str:
        """计算缓存 key = SHA256(query_text + recall_config_json)。

        Args:
            query_text: 查询文本
            recall_config_json: recall 阶段配置的 JSON 序列化（需稳定排序以保证确定性）

        Returns:
            64 字符的 SHA256 hex 摘要
        """
        raw = query_text + recall_config_json
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ── 存取操作 ──────────────────────────────────────────────────

    def get(
        self, query_text: str, recall_config_json: str
    ) -> Optional[list[CachedChunk]]:
        """查询缓存。

        Args:
            query_text: 查询文本
            recall_config_json: recall 配置的 JSON 字符串

        Returns:
            命中则返回 CachedChunk 列表，未命中返回 None
        """
        key = self.compute_key(query_text, recall_config_json)
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT chunk_ids, scores, contents, doc_ids FROM recall_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()

        if row is None:
            self._misses += 1
            return None

        self._hits += 1
        chunk_ids = json.loads(row[0])
        scores = json.loads(row[1])
        contents = json.loads(row[2])
        doc_ids = json.loads(row[3])

        return [
            CachedChunk(
                chunk_id=cid,
                score=score,
                content=content,
                doc_id=doc_id,
            )
            for cid, score, content, doc_id in zip(
                chunk_ids, scores, contents, doc_ids
            )
        ]

    def put(
        self,
        query_text: str,
        recall_config_json: str,
        chunks: list[CachedChunk],
    ) -> None:
        """写入缓存。

        Args:
            query_text: 查询文本
            recall_config_json: recall 配置的 JSON 字符串
            chunks: 要缓存的 chunk 列表
        """
        key = self.compute_key(query_text, recall_config_json)
        chunk_ids = json.dumps([c.chunk_id for c in chunks], ensure_ascii=False)
        scores = json.dumps([c.score for c in chunks], ensure_ascii=False)
        contents = json.dumps([c.content for c in chunks], ensure_ascii=False)
        doc_ids = json.dumps([c.doc_id for c in chunks], ensure_ascii=False)
        pipeline_hash = hashlib.sha256(
            recall_config_json.encode("utf-8")
        ).hexdigest()[:16]

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO recall_cache
                   (cache_key, query_text, pipeline_hash, chunk_ids, scores, contents, doc_ids)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (key, query_text, pipeline_hash, chunk_ids, scores, contents, doc_ids),
            )
            conn.commit()

    # ── 统计 ──────────────────────────────────────────────────────

    def stats(self) -> tuple[int, int]:
        """返回 (hits, misses) 计数。"""
        return self._hits, self._misses

    @property
    def total_entries(self) -> int:
        """返回缓存表中的条目总数。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM recall_cache").fetchone()
            return row[0] if row else 0
