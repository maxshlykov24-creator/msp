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


def init_db() -> None:
    from app import models  # noqa: F401
    from sqlalchemy import text

    eng = get_engine()
    models.Base.metadata.create_all(bind=eng)
    # Простые "миграции" под Postgres — добавляем недостающие колонки идемпотентно.
    migrations = [
        "ALTER TABLE conversation_map ADD COLUMN IF NOT EXISTS talkme_dialog_id VARCHAR(64)",
        # Если есть старые записи — заполняем talkme_dialog_id из external_conversation_id ("tm-N" -> "N").
        "UPDATE conversation_map "
        "SET talkme_dialog_id = SUBSTRING(external_conversation_id FROM 4) "
        "WHERE talkme_dialog_id IS NULL AND external_conversation_id LIKE 'tm-%'",
        "CREATE INDEX IF NOT EXISTS ix_conv_client_ref ON conversation_map (talkme_client_ref)",
        # Метки визита: рекламные параметры первого касания и связь со сделкой amoCRM.
        "ALTER TABLE conversation_map ADD COLUMN IF NOT EXISTS tracking JSON DEFAULT '{}'::json",
        "ALTER TABLE conversation_map ADD COLUMN IF NOT EXISTS client_phone VARCHAR(16)",
        "ALTER TABLE conversation_map ADD COLUMN IF NOT EXISTS amo_lead_id INTEGER",
        "ALTER TABLE conversation_map ADD COLUMN IF NOT EXISTS tracking_applied_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_conv_client_phone ON conversation_map (client_phone)",
        "CREATE INDEX IF NOT EXISTS ix_conv_amo_lead ON conversation_map (amo_lead_id)",
    ]
    with eng.begin() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
            except Exception:
                pass
