from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncState(Base):
    """Курсоры синхронизации и служебные метки (last_success_at, last_error и т.п.)."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Snapshot(Base):
    """Готовые JSON-срезы (главный — section='daily' с полным объектом DAILY)."""

    __tablename__ = "snapshot"

    section: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LeadSnapshot(Base):
    """Обезличенный снимок сделки воронки «Продажи» DIVO (без ПДн клиента).

    Перезаписывается целиком при каждой синхронизации по её `lead_id` —
    хранит только business-поля, нужные для пересборки агрегатов.
    """

    __tablename__ = "lead_snapshot"

    lead_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, index=True)
    responsible_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)  # unix ts
    updated_at: Mapped[int] = mapped_column(BigInteger, default=0)
    closed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    price: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trade_in: Mapped[bool] = mapped_column(Boolean, default=False)
    agreed: Mapped[bool] = mapped_column(Boolean, default=False)
    loss_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StageReached(Base):
    """Первый подтверждённый момент, когда сделка достигла статуса (из events API).

    Уникальная пара (lead_id, status_id). Используется для исторической
    воронки «сколько сделок реально прошли через этап», не только текущий срез.
    """

    __tablename__ = "stage_reached"
    __table_args__ = (UniqueConstraint("lead_id", "status_id", name="uq_stage_reached"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, index=True)
    pipeline_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, index=True)
    first_reached_at: Mapped[int] = mapped_column(BigInteger)  # unix ts


class MetricDaily(Base):
    """Резервная плоская таблица (не основной контракт — см. Snapshot.daily)."""

    __tablename__ = "metric_daily"
    __table_args__ = (UniqueConstraint("day", "scope", "metric", name="uq_metric_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (МСК)
    scope: Mapped[str] = mapped_column(String(64), index=True, default="all")
    metric: Mapped[str] = mapped_column(String(64), index=True)
    plan: Mapped[float | None] = mapped_column(Float, nullable=True)
    fact: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
