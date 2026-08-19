from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncState(Base):
    """Курсоры синхронизации и служебные метки (last_success_at и т.п.)."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Snapshot(Base):
    """Готовые JSON-срезы по секциям дашборда (overview, managers, funnels, ...)."""

    __tablename__ = "snapshot"

    section: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MetricDaily(Base):
    """Подневные агрегаты для таблицы и графиков динамики."""

    __tablename__ = "metric_daily"
    __table_args__ = (UniqueConstraint("day", "scope", "metric", name="uq_metric_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (МСК)
    scope: Mapped[str] = mapped_column(String(64), index=True, default="all")  # all | <user_id>
    metric: Mapped[str] = mapped_column(String(64), index=True)
    plan: Mapped[float | None] = mapped_column(Float, nullable=True)
    fact: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
