from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory():
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _upgrade_loyalty_members_schema() -> None:
    from sqlalchemy import inspect, text

    eng = get_engine()
    if not str(eng.url).startswith("postgresql"):
        return
    insp = inspect(eng)
    if "loyalty_members" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("loyalty_members")}
    alters: list[str] = []
    if "birth_month" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN birth_month INTEGER")
    if "birth_day" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN birth_day INTEGER")
    if "last_birthday_bonus_year" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN last_birthday_bonus_year INTEGER")
    if "tier_floor" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN tier_floor INTEGER NOT NULL DEFAULT 0")
    if "is_blocked" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT FALSE")
    if "last_synced_cp_attrs" not in cols:
        alters.append("ALTER TABLE loyalty_members ADD COLUMN last_synced_cp_attrs JSONB")
    if not alters:
        return
    with eng.begin() as conn:
        for sql in alters:
            conn.execute(text(sql))


def _upgrade_processed_events_schema() -> None:
    from sqlalchemy import inspect, text

    eng = get_engine()
    if not str(eng.url).startswith("postgresql"):
        return
    insp = inspect(eng)
    if "processed_events" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("processed_events")}
    alters: list[str] = []
    if "spent_amount" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN spent_amount INTEGER NOT NULL DEFAULT 0")
    if "last_spend_intent" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN last_spend_intent INTEGER NOT NULL DEFAULT 0")
    if "spend_pending" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN spend_pending BOOLEAN NOT NULL DEFAULT FALSE")
    if "spend_bonustransaction_ids" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN spend_bonustransaction_ids JSONB")
    if "spend_extra_discounts" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN spend_extra_discounts JSONB")
    if "last_synced_attrs" not in cols:
        alters.append("ALTER TABLE processed_events ADD COLUMN last_synced_attrs JSONB")
    if not alters:
        return
    with eng.begin() as conn:
        for sql in alters:
            conn.execute(text(sql))


def _upgrade_bonus_batches_schema() -> None:
    from sqlalchemy import inspect, text

    eng = get_engine()
    if not str(eng.url).startswith("postgresql"):
        return
    insp = inspect(eng)
    if "bonus_batches" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bonus_batches")}
    alters: list[str] = []
    if "activates_at" not in cols:
        alters.append("ALTER TABLE bonus_batches ADD COLUMN activates_at TIMESTAMPTZ")
        # NULL для существующих → активны сразу (legacy-семантика сохранена)
    if not alters:
        return
    with eng.begin() as conn:
        for sql in alters:
            conn.execute(text(sql))


def init_db() -> None:
    from app import models  # noqa: F401

    get_engine()
    models.Base.metadata.create_all(bind=_engine)
    _upgrade_loyalty_members_schema()
    _upgrade_processed_events_schema()
    _upgrade_bonus_batches_schema()
