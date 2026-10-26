import os
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (Boolean, DateTime, Integer, JSON, Numeric, String, Text,
                        UniqueConstraint, create_engine, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Item(Base):
    __tablename__ = 'items'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    model: Mapped[str] = mapped_column(String(200))
    color: Mapped[str] = mapped_column(String(100), default='')
    size: Mapped[str] = mapped_column(String(50), default='')
    category: Mapped[str] = mapped_column(String(200), default='')
    kind: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(20))
    barcodes: Mapped[list] = mapped_column(JSON, default=list)
    photo: Mapped[str] = mapped_column(Text, default='')
    source: Mapped[dict] = mapped_column(JSON, default=dict)


class Balance(Base):
    __tablename__ = 'balances'
    __table_args__ = (UniqueConstraint('item_id', 'location'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    item_id: Mapped[str] = mapped_column(String(100), index=True)
    location: Mapped[str] = mapped_column(String(100))
    known: Mapped[bool] = mapped_column(Boolean, default=False)
    free: Mapped[float] = mapped_column(Numeric(18, 3), default=0)
    frozen: Mapped[float] = mapped_column(Numeric(18, 3), default=0)
    reserved: Mapped[float] = mapped_column(Numeric(18, 3), default=0)
    damaged: Mapped[float] = mapped_column(Numeric(18, 3), default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Movement(Base):
    __tablename__ = 'movements'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    item_id: Mapped[str] = mapped_column(String(100), index=True)
    location: Mapped[str] = mapped_column(String(100))
    bucket: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Numeric(18, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Entity(Base):
    __tablename__ = 'entities'
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)


class Operation(Base):
    __tablename__ = 'operations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class Outbox(Base):
    __tablename__ = 'outbox'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    topic: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='held')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def engine_for(url=None):
    url = url or os.environ.get('KROY_DATABASE_URL', '')
    if not url:
        raise RuntimeError('Set KROY_DATABASE_URL; production requires PostgreSQL')
    return create_engine(url, pool_pre_ping=True,
                         connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite') else {})


@contextmanager
def transaction(engine):
    # Serialize short warehouse commands across ALL workers, including import/worker.
    # Read endpoints don't acquire this lock. Upgrade to per-aggregate locks if load requires.
    with Session(engine, expire_on_commit=False) as session:
        try:
            if engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            else:
                session.execute(text('SELECT pg_advisory_xact_lock(17923601)'))
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
