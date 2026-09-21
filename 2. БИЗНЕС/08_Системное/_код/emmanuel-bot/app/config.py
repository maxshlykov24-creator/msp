from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    # 0 = тест без группы: не проверяем участие и не постим напоминания/дайджесты в группу
    group_chat_id: int = 0
    # Четыре слэша после двоеточия — абсолютный путь для SQLAlchemy SQLite
    database_url: str = "sqlite+aiosqlite:////data/emmanuel.sqlite3"
    log_level: str = "INFO"

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # MUTE: в группу и чужие лички не пишем. Только личка ADMIN_TG_USER_ID.
    outbound_mute: bool = True
    jobs_enabled: bool = False
    admin_tg_user_id: int = 435207481


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
