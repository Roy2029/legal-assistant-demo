"""词典服务（D02 §4.8）：内置法律词典 + 用户自定义关键词（查询期生效）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import jieba

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_LEXICON = Path("D:/个人/Research/RAG1.0/experiments/data/legal_dict.txt")
DB_PATH = PROJECT_ROOT / "data" / "sqlite.db"

_builtin_loaded = False


def load_builtin_lexicon() -> int:
    """加载内置法律词典到 jieba（幂等）。"""
    global _builtin_loaded
    if _builtin_loaded:
        return 0
    count = 0
    if BUILTIN_LEXICON.exists():
        with open(BUILTIN_LEXICON, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                word = parts[0]
                freq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10000
                tag = parts[2] if len(parts) > 2 else "n"
                jieba.add_word(word, freq=freq, tag=tag)
                count += 1
    _builtin_loaded = True
    return count


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def apply_user_lexicon() -> int:
    """把用户启用词典加载到 jieba（查询期调用）。"""
    load_builtin_lexicon()
    try:
        conn = _conn()
        rows = conn.execute("SELECT term FROM user_lexicon WHERE enabled=1").fetchall()
        conn.close()
    except Exception:
        return 0
    for (term,) in rows:
        jieba.add_word(term.strip(), freq=100000, tag="n")
    return len(rows)


def add_term(term: str) -> None:
    term = term.strip()
    if not term:
        return
    conn = _conn()
    conn.execute("INSERT OR IGNORE INTO user_lexicon (term, enabled) VALUES (?, 1)", (term,))
    conn.commit()
    conn.close()


def remove_term(term: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM user_lexicon WHERE term=?", (term.strip(),))
    conn.commit()
    conn.close()


def set_enabled(term: str, enabled: bool) -> None:
    conn = _conn()
    conn.execute("UPDATE user_lexicon SET enabled=? WHERE term=?", (1 if enabled else 0, term.strip()))
    conn.commit()
    conn.close()


def list_terms() -> list[dict]:
    try:
        conn = _conn()
        rows = conn.execute("SELECT term, enabled FROM user_lexicon ORDER BY id DESC").fetchall()
        conn.close()
    except Exception:
        return []
    return [{"term": t, "enabled": bool(e)} for t, e in rows]
