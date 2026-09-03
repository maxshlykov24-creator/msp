"""Загрузка конфигурации из .env / окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv не обязателен на проде (systemd EnvironmentFile)
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    telegram_token: str
    nexara_api_key: str
    data_dir: Path
    log_dir: Path
    messenger: str
    match_threshold: int
    llm_fallback_enabled: bool
    groq_api_key: str
    groq_model: str
    backup_chat_id: str
    max_whitelist: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kombikorm.db"


def load_config() -> Config:
    data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
    log_dir = Path(os.getenv("LOG_DIR", str(data_dir / "logs"))).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        nexara_api_key=os.getenv("NEXARA_API_KEY", "").strip(),
        data_dir=data_dir,
        log_dir=log_dir,
        messenger=os.getenv("MESSENGER", "telegram").strip().lower(),
        match_threshold=_int("MATCH_THRESHOLD", 72),
        llm_fallback_enabled=os.getenv("LLM_FALLBACK_ENABLED", "0").strip() == "1",
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip(),
        backup_chat_id=os.getenv("BACKUP_CHAT_ID", "").strip(),
        max_whitelist=_int("MAX_WHITELIST", 2),
    )
