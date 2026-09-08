from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        ...,
        description="SQLAlchemy URL, e.g. postgresql+psycopg2://user:pass@db:5432/bridge",
    )

    # amo OAuth (маркет-интеграция)
    amo_client_id: str = ""
    amo_client_secret: str = ""
    amo_subdomain: str = "mansband"
    amo_base_domain: str = "amocrm.ru"
    # Опционально: долгосрочный токен из карточки, пока нет refresh в БД
    amo_long_lived_token: str = ""
    redirect_uri: str = "https://mansband-chat-bridge.twc1.net/oauth/callback"
    # в .env: REDIRECT_URI=...

    # API чатов (после ответа ТП)
    amojo_host: str = "https://amojo.amocrm.ru"
    amo_channel_id: str = ""
    amo_channel_secret: str = ""
    # После connect (можно вписать в .env)
    amo_scope_id: str = ""

    # Внутренняя ручка connect
    internal_secret: str = ""

    # Публичный адрес моста (для регистрации вебхука amoCRM add_lead)
    bridge_public_base: str = "https://mansband-chat-bridge.twc1.net"

    # Talk-me REST (токен из ЛК) и опциональные пути к JSON в вебхуке
    talkme_api_base: str = "https://lcab.talk-me.ru"
    talkme_rest_token: str = ""
    # POST относительно talkme_api_base, см. json-doc в кабинете
    talkme_send_message_path: str = "/public/api/online/v1/visitor/message"
    talkme_webhook_secret: str = ""
    # Логин оператора Talk-me (для sendToClient → operator.login)
    talkme_operator_login: str = ""
    # Диагностика: логировать начало тела POST /webhooks/talkme (ПДн — выключить после отладки)
    talkme_raw_log: bool = False

    # Страхующий поллинг Talk-me REST API
    talkme_poll_enabled: bool = False
    talkme_poll_interval_sec: int = 30
    talkme_poll_lookback_sec: int = 90
    talkme_poll_initial_lookback_sec: int = 300
    # Часовой пояс аккаунта Talk-me (МСК = +3)
    talkme_account_tz_offset_hours: int = 3

    # Имя бота/канала в payload для new_message (косметика)
    integration_title: str = "MansbandTalkmeBridge"


@lru_cache
def get_settings() -> Settings:
    return Settings()
