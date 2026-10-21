from __future__ import annotations

import datetime as dt
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

VISIT_ID_START = 900_000_000


def _now() -> dt.datetime:
    return dt.datetime.now(timezone.utc)


class Visit(Base):
    """Визит сайта. id из отдельного диапазона, чтобы не совпасть с Roistat."""

    __tablename__ = "ct_visit"

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=VISIT_ID_START), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    yclid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ym_uid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referrer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    landing_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)


class PoolNumber(Base):
    """Номер пула, статичный номер органики или номер канала."""

    __tablename__ = "ct_number"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pool, organic, channel
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    held_by_visit_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    held_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Assignment(Base):
    """Кто держал номер. После перевыдачи звонок всё ещё находится по этому окну."""

    __tablename__ = "ct_assignment"
    __table_args__ = (Index("ix_ct_assignment_number_started", "number_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    visit_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CallEvent(Base):
    """Входящий звонок Телфин. Сделку не создаёт."""

    __tablename__ = "ct_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    called: Mapped[str] = mapped_column(String(10), default="", index=True)
    caller: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {})
    visit_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    number_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    lead_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    alerted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {})
