from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cme_client_id: str = ""
    cme_client_secret: str = ""
    cme_base_url: str = "https://lk.cm.expert"
    cme_user_agent: str = "divo-cme-stock (info@msproduct.ru)"
    cme_publish_mode: str = "require"  # require | in_stock_only
    cme_publish_field: str = ""
    # В аккаунте несколько салонов (probe 27.08: DIVO 15371, Питер 27831 и др.).
    # Пусто = не фильтровать. Для таблицы виджета DIVO обязателен 15371.
    cme_dealer_id: str = ""

    google_sa_path: str = "google_sa.json"
    spreadsheet_id: str = "1icRyb3qObLM80b2S-zvwIttB808FIsDM8kB5Wu9u23Q"
    data_sheet: str = "Данные"
    # На складе, но не в продаже. Читает только бот, amo этот лист не видит.
    warehouse_sheet: str = "Склад"
    sheet1_name: str = "Sheet1"
    sheets_value_input: str = "RAW"

    state_path: str = "data/state.json"
    drop_ratio: float = 0.5
    allow_empty: bool = False

    tz: str = "Europe/Moscow"
    sync_interval_min: int = 15

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
