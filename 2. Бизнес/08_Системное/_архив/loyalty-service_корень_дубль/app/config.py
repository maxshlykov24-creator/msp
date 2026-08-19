from __future__ import annotations

from functools import lru_cache
from typing import FrozenSet

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_ids(value: str) -> FrozenSet[str]:
    if not value or not value.strip():
        return frozenset()
    return frozenset(x.strip() for x in value.split(",") if x.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ms_token: str
    api_base: str = "https://api.moysklad.ru/api/remap/1.2"
    database_url: str

    webhook_secret: str = ""
    # Публичный https://…/webhook/moysklad — для register_webhooks.py
    webhook_public_url: str = ""

    status_delivered: str = "Доставлен"
    status_return_full: str = "Возврат"
    status_return_partial: str = "Частичный возврат"

    loyalty_external_code: str = "loyalty-service"

    outlet_folder_ids: str = ""
    gift_folder_ids: str = ""
    delivery_folder_ids: str = ""

    attr_loyalty_tier: str = ""
    attr_annual_sum_rub: str = ""
    attr_enrolled_at: str = ""
    attr_last_tier_review: str = ""
    attr_tier_locked: str = ""

    bonus_delay_days: int = 15
    reconciliation_lookback_days: int = 2
    # 0 = отключено. Приветствие — при первом доставленном заказе; ДР — ежедневный job.
    welcome_bonus_points: int = 0
    birthday_bonus_points: int = 0
    # UUID атрибута контрагента «дата рождения» (опционально) — для sync_birthdays_from_moysklad
    attr_birthdate: str = ""

    @property
    def outlet_folder_ids_set(self) -> FrozenSet[str]:
        return _split_ids(self.outlet_folder_ids)

    @property
    def gift_folder_ids_set(self) -> FrozenSet[str]:
        return _split_ids(self.gift_folder_ids)

    @property
    def delivery_folder_ids_set(self) -> FrozenSet[str]:
        return _split_ids(self.delivery_folder_ids)


@lru_cache
def get_settings() -> Settings:
    return Settings()
