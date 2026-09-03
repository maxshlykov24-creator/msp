"""Конфиг из окружения (.env). Все значения — см. .env.example."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Прокси (exit-node) ТОЛЬКО для зарубежных API — AssemblyAI и Telegram.
    # Нужен, если сервер в РФ и эти сервисы заблокированы. Пусто = ходим напрямую.
    https_proxy: str | None = None

    # AssemblyAI — один ключ покрывает и распознавание речи (ASR), и оценку (LLM Gateway).
    assemblyai_api_key: str | None = None
    asr_language: str = "ru"

    # Опциональная оценка звонка через LLM. False = режим «только транскрибация» (это база).
    llm_enabled: bool = False
    llm_model: str = "claude-sonnet-4-5-20250929"   # модель LLM Gateway, уточняется в .env
    preset: str = "coach"                            # coach (обратная связь) | auditor (баллы по SLA)
    company_context_path: str = "company_context.md"  # файл контекста компании (из карты смыслов = SSOT компании, собранной в М1–М2)

    # CRM — российские эндпоинты, ходим напрямую (без прокси).
    bitrix_webhook_base: str | None = None           # https://<portal>/rest/<user>/<secret>/
    amocrm_subdomain: str | None = None
    amocrm_long_token: str | None = None
    crm_writeback: bool = False                       # дублировать результат примечанием в CRM

    # Telegram — куда падает транскрипт/оценка.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


settings = Settings()
