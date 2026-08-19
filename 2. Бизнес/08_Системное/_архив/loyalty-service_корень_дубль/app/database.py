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
