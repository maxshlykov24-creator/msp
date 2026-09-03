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

    # Counterparty attributes (UUID из /entity/counterparty/metadata):
    attr_loyalty_tier: str = ""        # «Уровень» (текст)
    attr_loyalty_status: str = ""      # «Статус» (текст: Активен / Заблокирован)
    attr_active_bonuses: str = ""      # «Активные бонусы» (число)
    attr_pending_bonuses: str = ""     # «Ожидают активации» (число)
    attr_annual_sum_rub: str = ""      # legacy (если поле ещё существует — не критично)
    attr_enrolled_at: str = ""         # legacy
    attr_last_tier_review: str = ""    # legacy
    attr_tier_locked: str = ""         # legacy

    # Customer order attributes (UUID из /entity/customerorder/metadata):
    attr_order_loyalty_status: str = ""    # «Статус ПЛ» (текст: Активен / Заблокирован)
    attr_order_loyalty_tier: str = ""      # «Уровень ПЛ» (текст)
    attr_order_active_bonuses: str = ""    # «Активно бонусов» (число; readonly для менеджера)
    attr_order_spend_bonuses: str = ""     # «Списано бонусов» (число; ввод менеджера + наш cap)
    attr_order_loyalty_comment: str = ""   # «Комментарии ПЛ» (текст)

    # CustomEntity dictionaries behind attributes «Статус»/«Уровень».
    # Defaults are from current MartaChe MoySklad account.
    attr_status_customentity_id: str = "294b0f14-42d9-11f1-0a80-04e30042a759"
    attr_tier_customentity_id: str = "604ac308-42d9-11f1-0a80-0f9700432c4b"

    bonus_delay_days: int = 15
    reconciliation_lookback_days: int = 2
    # 0 = отключено. Приветствие при ручной регистрации в заказе; ДР — ежедневный job.
    registration_welcome_bonus_points: int = 300
    welcome_bonus_points: int = 0
    birthday_bonus_points: int = 0
    # UUID атрибута контрагента «дата рождения» (опционально) — для sync_birthdays_from_moysklad
    attr_birthdate: str = ""

    # Лимит списания бонусов (% от итоговой суммы заказа)
    loyalty_spend_percent_limit: int = 30
    # Сколько последних строк хранить в «Комментарии ПЛ»
    loyalty_comment_max_lines: int = 12
    # Часовой пояс для штампов в комментариях (Москва UTC+3)
    loyalty_comment_tz_offset_minutes: int = 180

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
