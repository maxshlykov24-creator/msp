"""Подключение к PostgreSQL, сессии, advisory-локи по телефону."""
from __future__ import annotations

import contextlib
import hashlib
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    """Ленивое создание engine: не тянет драйвер БД при импорте модуля
    (важно для оффлайн-тестов на sqlite, где psycopg2 не установлен)."""
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionLocal


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _phone_lock_key(phone: str) -> int:
    """Стабильный signed 64-bit ключ из телефона для pg_advisory_lock."""
    h = hashlib.sha1(phone.encode("utf-8")).digest()
    val = int.from_bytes(h[:8], "big", signed=False)
    # привести к диапазону signed bigint
    return val - (1 << 63)


@contextlib.contextmanager
def phone_lock(s: Session, phone: str | None) -> Iterator[None]:
    """Транзакционный advisory-лок по нормализованному телефону: защищает от
    гонок вебхуков одного клиента. Если телефона нет — лок не берём."""
    if not phone:
        yield
        return
    key = _phone_lock_key(phone)
    s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
    yield
