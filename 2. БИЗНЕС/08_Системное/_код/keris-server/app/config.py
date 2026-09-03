"""Конфигурация из окружения / .env. Секреты не хранить в коде."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'keris_dev.db'}"
    ))

    yclients_partner_token: str = field(default_factory=lambda: os.environ.get("YCLIENTS_PARTNER_TOKEN", ""))
    yclients_user_token: str = field(default_factory=lambda: os.environ.get("YCLIENTS_USER_TOKEN", ""))
    yclients_company_id: str = field(default_factory=lambda: os.environ.get("YCLIENTS_COMPANY_ID", ""))
    yclients_application_id: str = field(default_factory=lambda: os.environ.get("YCLIENTS_APPLICATION_ID", ""))
    yclients_webhook_secret: str = field(default_factory=lambda: os.environ.get("YCLIENTS_WEBHOOK_SECRET", ""))

    # Доп. поля записи в YCLIENTS (Настройки → Дополнительные поля → «Ключ для API»).
    # Пока код не задан — соответствующее поле просто не читается/не пишется, ничего не ломается.
    yclients_cf_pet_name: str = field(default_factory=lambda: os.environ.get("YCLIENTS_CF_PET_NAME", ""))
    yclients_cf_pet_breed: str = field(default_factory=lambda: os.environ.get("YCLIENTS_CF_PET_BREED", ""))
    yclients_cf_pet_weight: str = field(default_factory=lambda: os.environ.get("YCLIENTS_CF_PET_WEIGHT", ""))
    yclients_cf_pet_birth_date: str = field(
        default_factory=lambda: os.environ.get("YCLIENTS_CF_PET_BIRTH_DATE", "")
    )

    # Домен аккаунта, а не api_domain из токена: на api-b.amocrm.ru тот же
    # долгосрочный токен отвечает 401 Account not found — из-за этого в июле
    # решили, что подписка не оплачена, и выключили интеграцию целиком.
    amocrm_base_url: str = field(default_factory=lambda: os.environ.get("AMOCRM_BASE_URL", "https://kerisclub.amocrm.ru"))
    amocrm_token: str = field(default_factory=lambda: os.environ.get("AMOCRM_LONG_LIVED_TOKEN", ""))
    amocrm_pipeline_grooming_id: str = field(default_factory=lambda: os.environ.get("AMOCRM_PIPELINE_GROOMING_ID", "11225138"))
    amocrm_field_client_grooming: str = field(default_factory=lambda: os.environ.get("AMOCRM_FIELD_CLIENT_GROOMING", "1820905"))

    # Порог «спящего» и «потерянного» клиента для метрики на контакте.
    amocrm_sleeping_days: int = field(default_factory=lambda: int(os.environ.get("AMOCRM_SLEEPING_DAYS", "60")))
    amocrm_lost_days: int = field(default_factory=lambda: int(os.environ.get("AMOCRM_LOST_DAYS", "120")))

    karina_bot_token: str = field(default_factory=lambda: os.environ.get("KARINA_BOT_TOKEN", ""))
    karina_telegram_ids: list[str] = field(default_factory=lambda: _list("KARINA_TELEGRAM_IDS"))

    # Отдельный бот-уведомитель для более широкого круга админов (@kerisclub_notify_bot).
    # Только push, без команд — список получателей пополняется вручную по chat_id.
    admin_notify_bot_token: str = field(default_factory=lambda: os.environ.get("ADMIN_NOTIFY_BOT_TOKEN", ""))
    admin_notify_chat_ids: list[str] = field(default_factory=lambda: _list("ADMIN_NOTIFY_CHAT_IDS"))

    # Клиентский бот (Mini App) — через него уходят напоминания владельцам питомцев.
    client_bot_token: str = field(default_factory=lambda: os.environ.get("CLIENT_BOT_TOKEN", ""))
    max_bot_token: str = field(default_factory=lambda: os.environ.get("MAX_BOT_TOKEN", ""))
    reminders_enabled: bool = field(default_factory=lambda: _bool("REMINDERS_ENABLED", True))

    # Куда отправлять клиента за кодом входа и напоминаниями, если бот не привязан.
    # Username'ы, а не токены: уходят в ответ API и в admin_note журнала YCLIENTS.
    client_bot_username: str = field(default_factory=lambda: os.environ.get("CLIENT_BOT_USERNAME", "kerisclubbot"))
    max_bot_username: str = field(default_factory=lambda: os.environ.get("MAX_BOT_USERNAME", "id775149185013_bot"))

    # Бот фото-отчётов для администратора (@kerisclubphotobot) — свой whitelist,
    # отдельно от бота Карины: у неё управление, у администратора только фото.
    staff_bot_token: str = field(default_factory=lambda: os.environ.get("STAFF_BOT_TOKEN", ""))
    staff_telegram_ids: list[str] = field(default_factory=lambda: _list("STAFF_TELEGRAM_IDS"))

    # Фото до/после лежат у нас, а не ссылкой на Telegram (file_path живёт ~час).
    photos_dir: str = field(default_factory=lambda: os.environ.get("PHOTOS_DIR", str(BASE_DIR / "photos")))
    media_base_url: str = field(default_factory=lambda: os.environ.get("MEDIA_BASE_URL", "/media").rstrip("/"))
    # Внешний адрес сайта записи. Нужен там, где ссылку открывают не в браузере
    # клиента: фото-отчёт в карточке amoCRM по относительному `/media/...` не открыть.
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"))

    # Возврат «давно не был»: клиенту без визита дольше N дней — одно сообщение.
    reactivation_days: int = field(default_factory=lambda: int(os.environ.get("REACTIVATION_DAYS", "60")))
    reactivation_enabled: bool = field(default_factory=lambda: _bool("REACTIVATION_ENABLED", True))

    # Секрет подписи кодов входа. Раньше брался из ключа SMS-провайдера — появление
    # ключа разом инвалидировало все живые коды. Теперь свой, независимый.
    otp_secret: str = field(default_factory=lambda: os.environ.get("OTP_SECRET", ""))

    # SMS-уведомления (targetsms.ru) — «спасибо за запись» + напоминания за 24ч/3ч,
    # шлются всем клиентам параллельно с Telegram/MAX (не заменяют их).
    # TARGETSMS_ENABLED=false по умолчанию — платный канал, включать явно.
    # Авторизация — Bearer-токен из кабинета, не логин/пароль входа на сайт.
    # Имя отправителя обязательное: без него шлюз отвечает «Нет отправителя».
    targetsms_enabled: bool = field(default_factory=lambda: _bool("TARGETSMS_ENABLED", False))
    targetsms_token: str = field(default_factory=lambda: os.environ.get("TARGETSMS_TOKEN", ""))
    targetsms_sender: str = field(default_factory=lambda: os.environ.get("TARGETSMS_SENDER", "KerisClub"))

    admin_api_key: str = field(default_factory=lambda: os.environ.get("ADMIN_API_KEY", ""))
    host: str = field(default_factory=lambda: os.environ.get("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8091")))

    @property
    def yclients_ready(self) -> bool:
        return bool(self.yclients_partner_token and self.yclients_user_token and self.yclients_company_id)

    @property
    def amocrm_ready(self) -> bool:
        return bool(self.amocrm_token)

    @property
    def targetsms_ready(self) -> bool:
        return bool(self.targetsms_enabled and self.targetsms_token and self.targetsms_sender)

    @property
    def telegram_bot_url(self) -> str:
        return f"https://t.me/{self.client_bot_username}?start=auth"

    @property
    def max_bot_url(self) -> str:
        return f"https://max.ru/{self.max_bot_username}"


settings = Settings()
