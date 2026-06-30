from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_async_engine(url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def _migrate_sqlite_add_user_columns(sync_conn) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_bot_admin" not in cols:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN is_bot_admin BOOLEAN NOT NULL DEFAULT 0"))


def _migrate_sqlite_reports_add_bible_days(sync_conn) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if not insp.has_table("reports"):
        return
    cols = {c["name"] for c in insp.get_columns("reports")}
    if "q1b_bible_days" not in cols:
        sync_conn.execute(text("ALTER TABLE reports ADD COLUMN q1b_bible_days VARCHAR(32) DEFAULT '—'"))


def _migrate_sqlite_users_add_ai_dossier(sync_conn) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "ai_dossier" not in cols:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN ai_dossier TEXT"))


async def init_db() -> None:
    from app import models as _models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if get_settings().database_url.startswith("sqlite"):
            await conn.run_sync(_migrate_sqlite_add_user_columns)
            await conn.run_sync(_migrate_sqlite_reports_add_bible_days)
            await conn.run_sync(_migrate_sqlite_users_add_ai_dossier)
