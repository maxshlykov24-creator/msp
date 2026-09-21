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

    # MUTE: в группу не пишем. Личка админа и coverage_dm_user_ids (Андрей) разрешена.
    outbound_mute: bool = True
    jobs_enabled: bool = False
    admin_tg_user_id: int = 435207481
    # Сводка с никами в личку: Андрей Николаев. Админ добавляется всегда.
    coverage_dm_user_ids: str = "256161124"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
