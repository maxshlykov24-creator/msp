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


class CallRecord(Base):
    __tablename__ = "call_record"
    __table_args__ = (UniqueConstraint("account_id", "call_key", name="uq_call_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    call_key: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[int] = mapped_column(BigInteger, index=True)
    direction: Mapped[str] = mapped_column(String(16))
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(128), default="")
    entity_refs: Mapped[list] = mapped_column(JSONB, default=list)
    lead_ids: Mapped[list] = mapped_column(JSONB, default=list)
    owner_candidates: Mapped[list] = mapped_column(JSONB, default=list)
    manager_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    manager_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_url: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="waiting_recording", index=True)
    category: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    category_reason: Mapped[str] = mapped_column(Text, default="")
    is_calibration: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nexara_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transcript: Mapped[dict] = mapped_column(JSONB, default=dict)
    analysis: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation_errors: Mapped[list] = mapped_column(JSONB, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    yes_count: Mapped[int] = mapped_column(Integer, default=0)
    applicable_count: Mapped[int] = mapped_column(Integer, default=0)
    is_scored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(32), default="")
    analysis_version: Mapped[str] = mapped_column(String(32), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CallDelivery(Base):
    __tablename__ = "call_delivery"
    __table_args__ = (UniqueConstraint("call_id", "channel", "target", name="uq_call_delivery"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    remote_id: Mapped[str] = mapped_column(String(128), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
