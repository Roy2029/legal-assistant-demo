"""配置服务：读取本地配置，支持热更新（SPEC §4.2 / D03）。"""
import json
import threading
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "data" / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"


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
        "retrieval": {"top_k": 8, "recall_top_k": 50},
    }

class ConfigService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
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
            self._cache = cfg
            return cfg

    def save(self, cfg: dict[str, Any]) -> None:
        with self._lock:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cache = cfg

    def update(self, partial: dict[str, Any]) -> dict[str, Any]:
        cfg = self.load()
        cfg.update(partial)
        self.save(cfg)
        return cfg

    def get_llm(self) -> dict[str, Any]:
        return self.load().get("llm", {})

config_service = ConfigService()
