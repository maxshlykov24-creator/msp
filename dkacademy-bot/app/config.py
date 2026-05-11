from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        ...,
        description="SQLAlchemy URL, postgresql+psycopg2://...",
    )

    amo_client_id: str = ""
    amo_client_secret: str = ""
    amo_subdomain: str = "dkacademy"
    amo_base_domain: str = "amocrm.ru"
    amo_long_lived_token: str = ""
    redirect_uri: str = "https://dkacademy-bot.example/oauth/callback"

    internal_secret: str = ""

    telegram_bot_token: str = Field("", description="BotFather token")

    liveinform_api_id: str = ""
    liveinform_track_url: str = "https://www.liveinform.ru/api/v2/track/"
    liveinform_webhook_secret: str = ""
    liveinform_webhook_header: str = "X-LiveInform-Secret"

    amo_field_lead_cdek: int = 0
    amo_field_lead_liveinform_status: int = 0

    amo_order_entity: str = "lead_only"  # lead_only | catalog

    amo_catalog_id: int = 0
    amo_field_catalog_cdek: int = 0
    amo_field_catalog_liveinform_status: int = 0

    amo_field_contact_telegram_chat_id: int = 0
    amo_field_contact_telegram_user_id: int = 0
    amo_field_contact_bot_started_at: int = 0
    amo_field_contact_bot_started_at_type: str = "text"  # text | date

    onboarding_contact_not_found: str = "reject"  # create | reject

    manager_telegram_username: str = "dk_academy"

    tg_notify_template: str = (
        "📦 Обновление по вашему заказу\n"
        "Трек: {tracking}\n"
        "{status_text}"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
