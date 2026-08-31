"""配置服务：读取本地配置，支持热更新（SPEC §4.2 / D03）。

敏感字段（llm.api_key / wenshu.password / retrieval.reranker_api_key）加密落盘、
内存解密；对外提供 redacted() 脱敏视图，避免明文密钥经 /api/config 回传前端。
"""
import json
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "data" / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"

_MASKED = "***"

# 敏感字段声明：位置 → 字段名（redacted 脱敏、加密落盘、sentinel 剥离共用）
SECRET_FIELDS = [
    ("llm", "api_key"),
    ("wenshu", "password"),
    ("retrieval", "reranker_api_key"),
]


def _load_env_llm() -> dict[str, str]:
    """从项目根 .env 读取 LLM 配置（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）。"""
    env = {}
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
        import os
        env["api_key"] = os.getenv("LLM_API_KEY", "") or ""
        env["base_url"] = os.getenv("LLM_BASE_URL", "") or ""
        env["model"] = os.getenv("LLM_MODEL", "") or ""
    except Exception:
        pass
    return env


def _default_config() -> dict[str, Any]:
    llm_env = _load_env_llm()
    # DeepSeek 默认 model
    if llm_env.get("base_url") and not llm_env.get("model"):
        llm_env["model"] = "deepseek-chat"
    return {
        "llm": llm_env,
        "desensitize": {"enabled": True, "level": "standard"},
        "wenshu": {"username": "", "password": ""},
        "retrieval": {
            "top_k": 8,
            "recall_top_k": 50,
            "embedding_device": "cpu",
            "reranker_provider": "skip",  # skip | local | api
            "reranker_model": "D:/个人/Research/RAG1.0/local_model/bge-reranker-v2-m3",
            "reranker_api_url": "",
            "reranker_api_key": "",
            "reranker_api_model": "bge-reranker-v2-m3",
            "enable_rerank": False,
        },
    }


def _encrypt_missing(cfg: dict[str, Any]) -> bool:
    """把仍为明文的敏感字段加密到内存副本；返回是否有字段被加密（用于一次性迁移）。"""
    from .crypto import encrypt_secret

    changed = False
    for section, field in SECRET_FIELDS:
        sec = cfg.get(section) or {}
        value = sec.get(field) or ""
        if value and not value.startswith("enc:"):
            sec[field] = encrypt_secret(value)
            cfg[section] = sec
            changed = True
    return changed


def _decrypt_all(cfg: dict[str, Any]) -> None:
    """把敏感字段解密到内存（磁盘保持密文）。解密失败显式抛错，不静默返回密文。"""
    from .crypto import decrypt_secret

    for section, field in SECRET_FIELDS:
        sec = cfg.get(section) or {}
        value = sec.get(field) or ""
        if value:
            sec[field] = decrypt_secret(value)
            cfg[section] = sec


def _strip_sentinels(partial: dict[str, Any]) -> None:
    """删除 partial 中等于空串/*** 的敏感字段，使 update 保留原值而非覆盖为 sentinel。"""
    for section, field in SECRET_FIELDS:
        sec = partial.get(section)
        if isinstance(sec, dict) and sec.get(field) in (None, "", _MASKED):
            sec.pop(field, None)
            if not sec:
                partial.pop(section, None)


class ConfigService:
    def __init__(self) -> None:
        # RLock：update() 持锁后还会调 load()/save()，普通 Lock 会同线程二次加锁死锁
        self._lock = threading.RLock()
        self._cache: Optional[dict[str, Any]] = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return self._cache
            cfg = _default_config()
            if CONFIG_PATH.exists():
                try:
                    loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    cfg.update(loaded)
                except Exception:
                    pass
                # 一次性迁移：旧明文密钥 → 加密落盘（迁移前备份，成功后回写）
                try:
                    if _encrypt_missing(cfg):
                        bak = CONFIG_PATH.with_suffix(".json.bak")
                        shutil.copyfile(CONFIG_PATH, bak)
                        CONFIG_PATH.write_text(
                            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                except Exception:
                    pass
            _decrypt_all(cfg)
            self._cache = cfg
            return cfg

    def save(self, cfg: dict[str, Any]) -> None:
        with self._lock:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
            try:
                _encrypt_missing(write_cfg)
            except Exception:
                pass
            CONFIG_PATH.write_text(json.dumps(write_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cache = cfg

    def update(self, partial: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cfg = self.load()
            _strip_sentinels(partial)
            cfg.update(partial)
            self.save(cfg)
            return cfg

    def redacted(self) -> dict[str, Any]:
        """脱敏视图：敏感字段替换为 ***，并附加 *_set 布尔标记。供 /api/config 返回。"""
        cfg = self.load()
        c = json.loads(json.dumps(cfg, ensure_ascii=False))
        for section, field in SECRET_FIELDS:
            sec = c.get(section) or {}
            value = sec.get(field) or ""
            sec[field] = _MASKED
            sec[f"{field}_set"] = bool(value)
            c[section] = sec
        return c

    def get_llm(self) -> dict[str, Any]:
        return self.load().get("llm", {})

    def get_wenshu(self) -> dict[str, Any]:
        return self.load().get("wenshu", {})


config_service = ConfigService()
