"""SQLite 初始化与表结构（SPEC §5.2）。"""
from pathlib import Path
import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "sqlite.db"

_tables_ready = False


def get_engine() -> sa.Engine:
    global _tables_ready
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{DB_PATH}", echo=False)
    if not _tables_ready:
        with engine.begin() as conn:
            for ddl in TABLES.values():
                conn.execute(sa.text(ddl))
        _tables_ready = True
    return engine

TABLES: dict[str, str] = {
    "sessions": """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL DEFAULT 'chat',
            action TEXT,
            title TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "audit_logs": """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'local',
            session_id TEXT,
            mode TEXT,
            input_masked TEXT,
            output_summary TEXT,
            trace_id TEXT,
            model TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "user_kb": """
        CREATE TABLE IF NOT EXISTS user_kb (
            kb_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "user_docs": """
        CREATE TABLE IF NOT EXISTS user_docs (
            doc_id TEXT PRIMARY KEY,
            kb_id TEXT NOT NULL,
            file_path TEXT,
            parse_status TEXT DEFAULT 'pending',
            chunk_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "user_lexicon": """
        CREATE TABLE IF NOT EXISTS user_lexicon (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL UNIQUE,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "config": """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "law_meta": """
        CREATE TABLE IF NOT EXISTS law_meta (
            law_id TEXT PRIMARY KEY,
            law_name TEXT NOT NULL,
            version TEXT,
            status TEXT,
            source_url TEXT,
            publish_dept TEXT,
            publish_date TEXT,
            effective_date TEXT,
            structure_json TEXT,
            last_updated TEXT
        )""",
    "update_jobs": """
        CREATE TABLE IF NOT EXISTS update_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT,
            status TEXT,
            detail TEXT,
            started_at TEXT DEFAULT (datetime('now','localtime')),
            finished_at TEXT
        )""",
    "badcase_feedback": """
        CREATE TABLE IF NOT EXISTS badcase_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            trace_id TEXT,
            mode TEXT NOT NULL DEFAULT 'chat',
            action TEXT,
            query TEXT NOT NULL,
            answer TEXT,
            reason TEXT NOT NULL DEFAULT 'other',
            root_cause TEXT,
            note TEXT,
            trace_json TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "badcases": """
        CREATE TABLE IF NOT EXISTS badcases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT,
            session_id TEXT,
            query TEXT,
            answer TEXT,
            error_type_user TEXT,
            root_cause TEXT,
            ref_id TEXT,
            note TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "pipeline_traces": """
        CREATE TABLE IF NOT EXISTS pipeline_traces (
            trace_id TEXT PRIMARY KEY,
            session_id TEXT,
            query TEXT,
            rag_config_version TEXT,
            trace_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "contracts": """
        CREATE TABLE IF NOT EXISTS contracts (
            contract_id TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            file_type TEXT,
            status TEXT DEFAULT 'uploaded',
            redacted_path TEXT,
            mapping_path TEXT,
            report_path TEXT,
            risk_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
    "messages": """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            msg_kind TEXT,
            content TEXT,
            tool_calls TEXT,
            token_count INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
}

def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for name, ddl in TABLES.items():
            conn.execute(sa.text(ddl))
    engine.dispose()

if __name__ == "__main__":
    init_db()
    print(f"SQLite initialized at {DB_PATH}")
