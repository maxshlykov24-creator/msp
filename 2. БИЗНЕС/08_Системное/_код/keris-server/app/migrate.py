"""Догоняющие ALTER TABLE для колонок, добавленных после первого деплоя.

`Base.metadata.create_all` создаёт только новые таблицы и не меняет существующие,
а Alembic для этого объёма избыточен. Здесь — идемпотентное добавление колонок:
сверяем модель с фактической схемой и добавляем то, чего нет.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("keris.migrate")

# table -> (column, DDL-тип с DEFAULT)
COLUMNS: dict[str, list[tuple[str, str]]] = {
    "services": [
        ("durations", "JSON"),
        ("group", "VARCHAR(32) DEFAULT 'base'"),
        ("description", "VARCHAR(2000) DEFAULT ''"),
        ("includes", "VARCHAR(2000) DEFAULT ''"),
        ("yclients_service_ids", "JSON"),
    ],
    "addons": [
        ("prices", "JSON"),
        ("durations", "JSON"),
        ("group", "VARCHAR(64) DEFAULT ''"),
        ("yclients_service_id", "INTEGER"),
        ("yclients_service_ids", "JSON"),
    ],
    "bookings": [
        ("free_addon_ids", "JSON"),
        ("applied_promos", "JSON"),
        ("marketing_consent", "BOOLEAN DEFAULT FALSE"),
        ("media_consent", "BOOLEAN DEFAULT FALSE"),
        ("admin_note", "VARCHAR(500) DEFAULT ''"),
        ("telegram_chat_id", "BIGINT"),
        ("max_user_id", "BIGINT"),
        ("visits_charged", "FLOAT"),
        ("reminders_sent", "JSON"),
        ("client_confirmed_at", "TIMESTAMP"),
        ("pet_birth_date", "VARCHAR(20) DEFAULT ''"),
        ("amocrm_company_id", "INTEGER"),
        ("amocrm_stage", "VARCHAR(64) DEFAULT ''"),
        ("thanks_channels", "JSON"),
        ("thanks_pending", "BOOLEAN DEFAULT FALSE"),
    ],
    "sms_otps": [
        ("channel", "VARCHAR(16) DEFAULT ''"),
    ],
    "masters": [
        ("shift_on", "INTEGER DEFAULT 0"),
        ("shift_off", "INTEGER DEFAULT 0"),
        ("shift_start", "VARCHAR(10) DEFAULT ''"),
    ],
    "subscription_plans": [
        ("bonus_spa", "INTEGER DEFAULT 0"),
    ],
    "subscriptions": [
        ("bonus_spa_used", "INTEGER DEFAULT 0"),
        ("bonus_extra_used", "BOOLEAN DEFAULT FALSE"),
    ],
}


def ensure_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns:
                if name in present:
                    continue
                quoted = f'"{name}"'  # group — зарезервированное слово
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {quoted} {ddl_type}'))
                log.info("миграция: %s.%s добавлена", table, name)
    _widen_telegram_chat_id(engine)


def _widen_telegram_chat_id(engine: Engine) -> None:
    """Старая колонка bookings.telegram_chat_id была INTEGER: chat_id Telegram
    часто > 2_147_483_647 (пример 7251008149) — INSERT падал, запись из YCLIENTS
    не создавалась и клиент не получал «спасибо»."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        kind = conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'bookings' "
            "AND column_name = 'telegram_chat_id'"
        )).scalar()
        if kind == "integer":
            conn.execute(text("ALTER TABLE bookings ALTER COLUMN telegram_chat_id TYPE BIGINT"))
            log.info("миграция: bookings.telegram_chat_id INTEGER → BIGINT")
