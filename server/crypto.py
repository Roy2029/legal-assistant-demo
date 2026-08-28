"""本地敏感配置加密（Fernet）。密钥复用 data/keys/fernet.key，与脱敏模块同源。"""
from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = PROJECT_ROOT / "data" / "keys" / "fernet.key"

_PREFIX = "enc:"


def _get_key() -> bytes:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    return key


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith(_PREFIX):
        return plaintext
    f = Fernet(_get_key())
    return _PREFIX + f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    try:
        f = Fernet(_get_key())
        return f.decrypt(value[len(_PREFIX):].encode("utf-8")).decode("utf-8")
    except Exception:
        return value
