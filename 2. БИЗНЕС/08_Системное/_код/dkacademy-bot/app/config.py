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
    max_bot_token: str = Field("", description="MAX platform bot token")

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
    amo_field_contact_bot_active: int = 0  # Telegram чекбокс «Подписан на бот» (ID 2958307)
    amo_field_contact_max_chat_id: int = 0  # MAX chat_id (ID 2958317)
    amo_field_contact_max_active: int = 0  # MAX чекбокс «Подписан на бот» (ID 2958315)

    onboarding_contact_not_found: str = "reject"  # create | reject

    manager_telegram_username: str = "dk_academy"
    manager_max_url: str = ""  # прямая ссылка на менеджера в MAX (max.ru/u/…)

    tg_notify_template: str = (
        "📦 Обновление по вашему заказу\n\n"
        "Трек: {tracking}\n"
        "Статус: {status_text}"
    )

    # ── Автоматизация «Требует касания» (повторное касание) ──────────────────
    # Воркер: через N дней после Успеха шлёт напоминание + создаёт сделку в
    # воронке «Повторные продажи» на этапе «Требует касания», если у клиента
    # нет открытой сделки и он сам не написал.
    repeat_touch_enabled: bool = False  # включить фоновый воркер
    amo_pipeline_sales: int = 10694702  # воронка «Продажи»
    amo_pipeline_repeat: int = 10697570  # воронка «Повторные продажи»
    amo_status_won: int = 142  # «Успешно реализовано»
    amo_status_trebuet_kasaniya: int = 0  # этап «Требует касания» (дискавери)
    amo_field_contact_active: int = 0  # чекбокс контакта «Действующий» (дискавери, опц.)

    repeat_touch_msg_days: int = 35  # через сколько дней от Успеха — сообщение
    repeat_touch_deal_days: int = 40  # через сколько дней от Успеха — сделка
    repeat_touch_lookback_days: int = 60  # глубина выборки успешных сделок
    repeat_touch_interval_hours: int = 24  # период прогона воркера
    repeat_touch_message: str = (
        "Привет 🙂 Прошёл месяц — и если уход расходовался по-честному, остатки уже на донышке 🫧\n\n"
        "Когда шампунь приходится «выжимать с уговорами» — это он деликатно намекает, "
        "что пора пополнить запас ✨\n\n"
        "Мы рядом, поможем не прерывать ваш ритуал уюта 💛"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
