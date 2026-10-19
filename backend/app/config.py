from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "/data")))
    var_dir: Path = field(default_factory=lambda: Path(os.environ.get("VAR_DIR", "./var")))
    openrouter_api_key: str | None = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY") or None)
    openrouter_base_url: str = field(default_factory=lambda: os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "deepseek/deepseek-chat"))
    # Offline rule parser instead of the LLM. Used by tests; also used when no API key is set.
    llm_mock: bool = field(default_factory=lambda: _env_bool("LLM_MOCK", False))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static"))))

    @property
    def db_path(self) -> Path:
        return self.var_dir / "backtest.sqlite"

    @property
    def cache_dir(self) -> Path:
        return self.var_dir / "cache"

    @property
    def llm_mode(self) -> str:
        if self.llm_mock or not self.openrouter_api_key:
            return "offline-parser"
        return "openrouter"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.var_dir.mkdir(parents=True, exist_ok=True)
        _settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return _settings


def reset_settings(s: Settings | None = None) -> Settings:
    """Tests use this to point the app at temp folders."""
    global _settings
    _settings = s or Settings()
    _settings.var_dir.mkdir(parents=True, exist_ok=True)
    _settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return _settings
