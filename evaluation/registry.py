"""实验元数据注册表 — SQLite 实现。

全局注册实验和 Run 的元数据，支持：

- 实验级：创建、状态追踪、完成记录、环境快照
- Run 级：进度追踪、耗时、缓存统计、聚合指标
- 查询：跨实验清单、单实验详情、单 run 详情
- 导出：``metadata.json`` 实验快照

用法::

    from evaluation.registry import ExperimentsRegistry

    registry = ExperimentsRegistry()
    exp_id = registry.create_experiment("my-exp", "实验描述", config)
    run_id = registry.create_run(exp_id, "dense_only", "...", pipeline_config)
    registry.update_run_progress(run_id, 500, 2253)
    registry.complete_run(run_id, 123.4, summary_dict)
    registry.complete_experiment(exp_id, 3600.0)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认数据库路径（项目根目录下 experiments/experiments.db）
DEFAULT_DB_PATH = Path("experiments") / "experiments.db"


@dataclass
class ExperimentRecord:
    """实验记录的数据结构。"""
    id: int
    name: str
    description: str
    status: str  # pending | running | completed | error
    num_queries: int
    embedding_model: str
    llm_model: str
    code_version: str
    started_at: str
    completed_at: str | None
    duration_seconds: float | None
    error_message: str
    created_at: str


@dataclass
class RunRecord:
    """Run 记录的数据结构。"""
    id: int
    experiment_id: int
    run_name: str
    run_description: str
    status: str
    cache_hits: int
    cache_misses: int
    num_queries: int
    num_queries_total: int
    progress_pct: float
    started_at: str
    completed_at: str | None
    duration_seconds: float | None
    error_message: str
    summary_json: str | None


class ExperimentsRegistry:
    """实验元数据注册表（SQLite 实现）。

    所有实验共用同一数据库，按 experiment_id 区分。

    Args:
        db_path: SQLite 数据库路径，默认为 ``experiments/experiments.db``
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── 连接管理 ─────────────────────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        """获取或创建数据库连接。"""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── 初始化 ───────────────────────────────────────────────────

    def _init_db(self) -> None:
        """创建表结构（如不存在）。"""
        cursor = self.conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                description     TEXT DEFAULT '',
                config_yaml_hash TEXT DEFAULT '',
                config_yaml     TEXT DEFAULT '',
                dataset_queries TEXT DEFAULT '',
                dataset_qrels   TEXT DEFAULT '',
                index_path      TEXT DEFAULT '',
                index_db_name   TEXT DEFAULT '',
                embedding_model TEXT DEFAULT '',
                llm_model       TEXT DEFAULT '',
                code_version    TEXT DEFAULT '',
                num_queries     INTEGER DEFAULT 0,
                started_at      TEXT,
                completed_at    TEXT,
                duration_seconds REAL,
                status          TEXT DEFAULT 'pending'
                                CHECK(status IN ('pending','running','completed','error')),
                error_message   TEXT DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id   INTEGER NOT NULL
                                REFERENCES experiments(id) ON DELETE CASCADE,
                run_name        TEXT NOT NULL,
                run_description TEXT DEFAULT '',
                pipeline_config_json TEXT DEFAULT '',
                status          TEXT DEFAULT 'pending'
                                CHECK(status IN ('pending','running','completed','error')),
                started_at      TEXT,
                completed_at    TEXT,
                duration_seconds REAL,
                cache_hits      INTEGER DEFAULT 0,
                cache_misses    INTEGER DEFAULT 0,
                num_queries     INTEGER DEFAULT 0,
                num_queries_total INTEGER DEFAULT 0,
                progress_pct    REAL DEFAULT 0.0,
                error_message   TEXT DEFAULT '',
                summary_json    TEXT DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_experiments_name
                ON experiments(name);
            CREATE INDEX IF NOT EXISTS idx_runs_experiment_id
                ON runs(experiment_id);
        """)
        self.conn.commit()

    # ── 实验级操作 ───────────────────────────────────────────────

    def create_experiment(
        self,
        name: str,
        description: str = "",
        config: dict | None = None,
        embedding_model: str = "",
        llm_model: str = "",
        code_version: str = "",
    ) -> int:
        """创建新实验记录，状态设为 'pending'。

        Returns:
            新实验的 id
        """
        config_yaml_text = ""
        config_yaml_hash = ""
        dataset_queries = ""
        dataset_qrels = ""
        index_path = ""
        index_db_name = ""

        if config:
            from evaluation.config import ExperimentConfig
            if isinstance(config, ExperimentConfig):
                config_yaml_text = config.model_dump_json(indent=2)
                import hashlib
                config_yaml_hash = hashlib.sha256(
                    config_yaml_text.encode()
                ).hexdigest()[:12]
                dataset_queries = config.dataset.queries_path
                dataset_qrels = config.dataset.qrels_path
                index_path = config.index.path
                index_db_name = config.index.db_name
            elif isinstance(config, dict):
                config_yaml_text = json.dumps(config, ensure_ascii=False, indent=2)
                import hashlib
                config_yaml_hash = hashlib.sha256(
                    config_yaml_text.encode()
                ).hexdigest()[:12]

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO experiments
                (name, description, config_yaml_hash, config_yaml,
                 dataset_queries, dataset_qrels, index_path, index_db_name,
                 embedding_model, llm_model, code_version,
                 started_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now','localtime'), 'running')
        """, (
            name, description,
            config_yaml_hash, config_yaml_text,
            dataset_queries, dataset_qrels,
            index_path, index_db_name,
            embedding_model, llm_model, code_version,
        ))
        self.conn.commit()
        exp_id = cursor.lastrowid
        logger.info("实验已注册: id=%d, name=%s", exp_id, name)
        return exp_id

    def complete_experiment(
        self,
        exp_id: int,
        duration_seconds: float,
    ) -> None:
        """将实验标记为 completed，记录完成时间和耗时。"""
        self.conn.execute("""
            UPDATE experiments
            SET status = 'completed',
                completed_at = datetime('now','localtime'),
                duration_seconds = ?
            WHERE id = ?
        """, (duration_seconds, exp_id))
        self.conn.commit()
        logger.info("实验已完成: id=%d, duration=%.1fs", exp_id, duration_seconds)

    def update_experiment_status(
        self,
        exp_id: int,
        status: str,
        error_message: str = "",
    ) -> None:
        """更新实验状态（running / error）。"""
        self.conn.execute("""
            UPDATE experiments
            SET status = ?,
                error_message = CASE WHEN ? != '' THEN ? ELSE error_message END
            WHERE id = ?
        """, (status, error_message, error_message, exp_id))
        self.conn.commit()

    def update_experiment_queries(
        self,
        exp_id: int,
        num_queries: int,
    ) -> None:
        """更新实验的有效 query 数。"""
        self.conn.execute("""
            UPDATE experiments SET num_queries = ? WHERE id = ?
        """, (num_queries, exp_id))
        self.conn.commit()

    def get_experiment(self, exp_id: int) -> dict | None:
        """获取单个实验记录。"""
        cursor = self.conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_experiments(
        self,
        name_filter: str | None = None,
    ) -> list[dict]:
        """获取所有实验清单，按创建时间倒序。

        Args:
            name_filter: 可选的实验名称过滤（LIKE 匹配）

        Returns:
            实验记录列表
        """
        if name_filter:
            cursor = self.conn.execute(
                "SELECT * FROM experiments WHERE name LIKE ? ORDER BY created_at DESC",
                (f"%{name_filter}%",),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC"
            )
        return [dict(row) for row in cursor.fetchall()]

    # ── Run 级操作 ───────────────────────────────────────────────

    def create_run(
        self,
        exp_id: int,
        run_name: str,
        run_description: str = "",
        pipeline_config: dict | None = None,
        num_queries_total: int = 0,
    ) -> int:
        """创建新 Run 记录，状态设为 'running'。

        Returns:
            新 Run 的 id
        """
        pipeline_json = json.dumps(pipeline_config, ensure_ascii=False) if pipeline_config else ""

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO runs
                (experiment_id, run_name, run_description,
                 pipeline_config_json, num_queries_total,
                 started_at, status)
            VALUES (?, ?, ?, ?, ?,
                    datetime('now','localtime'), 'running')
        """, (
            exp_id, run_name, run_description,
            pipeline_json, num_queries_total,
        ))
        self.conn.commit()
        run_id = cursor.lastrowid
        logger.debug("Run 已注册: id=%d, experiment_id=%d, name=%s",
                     run_id, exp_id, run_name)
        return run_id

    def update_run_progress(
        self,
        run_id: int,
        num_queries: int,
        num_queries_total: int,
        cache_hits: int = 0,
        cache_misses: int = 0,
    ) -> None:
        """更新 Run 进度（query 处理数、缓存统计）。"""
        progress_pct = (num_queries / num_queries_total * 100) if num_queries_total > 0 else 0.0
        self.conn.execute("""
            UPDATE runs
            SET num_queries = ?,
                num_queries_total = ?,
                progress_pct = ?,
                cache_hits = ?,
                cache_misses = ?
            WHERE id = ?
        """, (num_queries, num_queries_total, progress_pct,
              cache_hits, cache_misses, run_id))
        self.conn.commit()

    def complete_run(
        self,
        run_id: int,
        duration_seconds: float,
        summary_json: dict | None = None,
    ) -> None:
        """将 Run 标记为 completed，记录耗时和指标摘要。"""
        summary_text = json.dumps(summary_json, ensure_ascii=False) if summary_json else ""
        self.conn.execute("""
            UPDATE runs
            SET status = 'completed',
                completed_at = datetime('now','localtime'),
                duration_seconds = ?,
                summary_json = ?
            WHERE id = ?
        """, (duration_seconds, summary_text, run_id))
        self.conn.commit()

    def update_run_status(
        self,
        run_id: int,
        status: str,
        error_message: str = "",
    ) -> None:
        """更新 Run 状态（可设为 error）。"""
        fields = ["status = ?"]
        values: list[Any] = [status]

        if status == "error" and error_message:
            fields.append("error_message = ?")
            values.append(error_message)

        values.append(run_id)
        self.conn.execute(
            f"UPDATE runs SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        self.conn.commit()

    def get_run(self, run_id: int) -> dict | None:
        """获取单个 Run 记录。"""
        cursor = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_runs_by_experiment(self, exp_id: int) -> list[dict]:
        """获取实验的所有 Run 记录。"""
        cursor = self.conn.execute(
            "SELECT * FROM runs WHERE experiment_id = ? ORDER BY id",
            (exp_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ── 查询 ─────────────────────────────────────────────────────

    def get_experiment_with_runs(self, name: str) -> dict | None:
        """按名称获取实验详情及所有 Run。"""
        cursor = self.conn.execute(
            "SELECT * FROM experiments WHERE name = ? ORDER BY created_at DESC LIMIT 1",
            (name,),
        )
        exp_row = cursor.fetchone()
        if exp_row is None:
            return None

        exp = dict(exp_row)
        exp["runs"] = self.get_runs_by_experiment(exp["id"])
        return exp

    def get_run_status(self, name: str, run_name: str) -> dict | None:
        """获取指定实验下指定 Run 的状态。"""
        cursor = self.conn.execute("""
            SELECT r.* FROM runs r
            JOIN experiments e ON r.experiment_id = e.id
            WHERE e.name = ? AND r.run_name = ?
            ORDER BY r.created_at DESC LIMIT 1
        """, (name, run_name))
        row = cursor.fetchone()
        return dict(row) if row else None

    # ── metadata.json 导出 ───────────────────────────────────────

    def export_metadata_json(
        self,
        exp_id: int,
        results_dir: str | Path | None = None,
    ) -> dict:
        """导出实验完整元数据为可序列化的 dict。

        用于生成 ``experiments/<name>/metadata.json``。

        Args:
            exp_id: 实验 id
            results_dir: 结果目录路径（写进 metadata 供查阅）

        Returns:
            包含实验信息和各 run 概要的 dict
        """
        exp = self.get_experiment(exp_id)
        if exp is None:
            return {"error": f"实验 id={exp_id} 不存在"}

        runs = self.get_runs_by_experiment(exp_id)

        # 解析 runs 的 summary_json
        run_summaries = []
        for r in runs:
            summary = {}
            if r.get("summary_json"):
                try:
                    summary = json.loads(r["summary_json"])
                except (json.JSONDecodeError, TypeError):
                    summary = {"_parse_error": True}

            run_summaries.append({
                "run_name": r["run_name"],
                "run_description": r["run_description"],
                "status": r["status"],
                "duration_seconds": r["duration_seconds"],
                "cache_hits": r["cache_hits"],
                "cache_misses": r["cache_misses"],
                "num_queries": r["num_queries"],
                "num_queries_total": r["num_queries_total"],
                "progress_pct": r["progress_pct"],
                "summary": summary,
            })

        result = {
            "experiment": {
                "name": exp["name"],
                "description": exp["description"],
                "status": exp["status"],
                "num_queries": exp["num_queries"],
                "embedding_model": exp["embedding_model"],
                "llm_model": exp["llm_model"],
                "code_version": exp["code_version"],
                "started_at": exp["started_at"],
                "completed_at": exp["completed_at"],
                "duration_seconds": exp["duration_seconds"],
            },
            "dataset": {
                "queries": exp["dataset_queries"],
                "qrels": exp["dataset_qrels"],
            },
            "index": {
                "path": exp["index_path"],
                "db_name": exp["index_db_name"],
            },
            "runs": run_summaries,
        }

        if results_dir:
            result["results_dir"] = str(results_dir)

        return result

    def write_metadata_json(
        self,
        exp_id: int,
        output_dir: str | Path,
    ) -> Path:
        """将实验元数据导出为 ``experiments/<name>/metadata.json``。

        Args:
            exp_id: 实验 id
            output_dir: 写入目录（通常是 ``experiments/<name>/``）

        Returns:
            写入的文件路径
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "metadata.json"

        data = self.export_metadata_json(exp_id, results_dir=str(output_dir / "results"))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("metadata.json 已导出: %s", out_path)
        return out_path

    # ── 代码版本 ─────────────────────────────────────────────────

    @staticmethod
    def get_code_version() -> str:
        """获取当前 Git commit hash（若无则返回空字符串）。"""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    # ── 上下文管理器 ──────────────────────────────────────────────

    def __enter__(self) -> "ExperimentsRegistry":
        return self

    def __exit__(self, *args) -> None:
        self.close()
