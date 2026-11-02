from __future__ import annotations

import json

from aiogram.fsm.storage.base import BaseStorage, StorageKey

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


def _migrate_sqlite_members_assigned_hashtag(sync_conn) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if not insp.has_table("group_members"):
        return
    cols = {c["name"] for c in insp.get_columns("group_members")}
    if "assigned_hashtag" not in cols:
        sync_conn.execute(text("ALTER TABLE group_members ADD COLUMN assigned_hashtag VARCHAR(80)"))


async def init_db() -> None:
    from app import models as _models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if get_settings().database_url.startswith("sqlite"):
            await conn.run_sync(_migrate_sqlite_add_user_columns)
            await conn.run_sync(_migrate_sqlite_reports_add_bible_days)
            await conn.run_sync(_migrate_sqlite_users_add_ai_dossier)
            await conn.run_sync(_migrate_sqlite_members_assigned_hashtag)
            await conn.run_sync(_migrate_sqlite_revelation_week)


def _migrate_sqlite_revelation_week(sync_conn) -> None:
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(sync_conn).get_columns("revelations")}
    if "report_week_start" not in cols:
        sync_conn.execute(text("ALTER TABLE revelations ADD COLUMN report_week_start DATE"))


class SQLiteFSMStorage(BaseStorage):
    """Сохраняет шаг и черновик в том же volume, что отчёты."""

    @staticmethod
    def _key(key: StorageKey) -> str:
        return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id,
                           key.business_connection_id, key.destiny])

    async def _read(self, key):
        from app.models import DialogState
        async with get_session_factory()() as session:
            row = await session.get(DialogState, self._key(key))
            return (row.state, json.loads(row.data)) if row else (None, {})

    async def set_state(self, key, state=None):
        from app.models import DialogState
        async with get_session_factory()() as session:
            row = await session.get(DialogState, self._key(key))
            if row is None:
                row = DialogState(key=self._key(key), data="{}")
                session.add(row)
            row.state = state.state if hasattr(state, "state") else state
            await session.commit()

    async def get_state(self, key):
        return (await self._read(key))[0]

    async def set_data(self, key, data):
        from app.models import DialogState
        async with get_session_factory()() as session:
            row = await session.get(DialogState, self._key(key))
            if row is None:
                row = DialogState(key=self._key(key))
                session.add(row)
            row.data = json.dumps(data, ensure_ascii=False)
            await session.commit()

    async def get_data(self, key):
        return (await self._read(key))[1]

    async def close(self):
        pass
