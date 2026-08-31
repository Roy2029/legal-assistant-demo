"""规则版可逆脱敏（D07）：送 LLM 前脱敏，最终交付物输出前还原。

映射持久化为 placeholder → original（无 kind 前缀），restore 直接查表还原；
占位符编号基于 map 内同类最大编号，跨调用全局唯一，避免不同原文串用同一占位符。
"""
from __future__ import annotations

import re
from pathlib import Path

from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
ANON_DIR = DATA_DIR / "anonymization"
KEY_PATH = DATA_DIR / "keys" / "fernet.key"

PATTERNS = [
    ("phone", re.compile(r"1[3-9]\d{9}")),
    ("idcard", re.compile(r"\d{17}[\dXx]")),
    ("bankcard", re.compile(r"\d{16,19}")),
    ("company", re.compile(r"[一-鿿]{2,20}(?:有限公司|有限责任公司|股份有限公司|集团)")),
]


def _get_key() -> bytes:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    return key


def _load_map() -> dict:
    ANON_DIR.mkdir(parents=True, exist_ok=True)
    f = Fernet(_get_key())
    map_file = ANON_DIR / "map.enc"
    if not map_file.exists():
        return {}
    try:
        import json
        data = f.decrypt(map_file.read_bytes())
        return json.loads(data)
    except Exception:
        return {}


def _save_map(mapping: dict) -> None:
    import json
    ANON_DIR.mkdir(parents=True, exist_ok=True)
    f = Fernet(_get_key())
    (ANON_DIR / "map.enc").write_bytes(f.encrypt(json.dumps(mapping, ensure_ascii=False).encode("utf-8")))


def _find_placeholder(mapping: dict, original: str) -> str | None:
    """查找原文已映射的占位符（跨调用复用，保证同一原文同一占位符）。"""
    for ph, orig in mapping.items():
        if orig == original:
            return ph
    return None


def _next_index(kind: str, mapping: dict) -> int:
    """同类占位符当前最大编号 + 1：占位符跨调用全局唯一。"""
    prefix = f"[{kind}"
    max_i = 0
    for ph in mapping:
        if ph.startswith(prefix) and ph.endswith("]"):
            try:
                max_i = max(max_i, int(ph[len(prefix):-1]))
            except ValueError:
                continue
    return max_i + 1


def desensitize(text: str) -> tuple[str, dict]:
    """返回 (脱敏文本, 本次新增映射 placeholder → original)。"""
    if not text:
        return text, {}
    mapping = _load_map()
    new_mapping = {}

    def repl(match, kind):
        original = match.group(0)
        existing = _find_placeholder(mapping, original)
        if existing:
            return existing
        placeholder = f"[{kind}{_next_index(kind, mapping)}]"
        mapping[placeholder] = original
        new_mapping[placeholder] = original
        return placeholder

    # 从长到短依次替换，避免公司名包含手机号等误伤（M0 简单顺序）
    out = text
    for kind, pat in PATTERNS:
        out = pat.sub(lambda m, k=kind: repl(m, k), out)
    if new_mapping:
        _save_map(mapping)
    return out, new_mapping


def restore(text: str) -> str:
    """把占位符还原为原文（仅最终交付物输出环节调用）。"""
    if not text:
        return text
    mapping = _load_map()
    for ph, original in mapping.items():
        text = text.replace(ph, original)
    return text


def apply_to_text(text: str) -> str:
    """仅脱敏（不返回映射）。"""
    out, _ = desensitize(text)
    return out
