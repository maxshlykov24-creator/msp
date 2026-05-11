from __future__ import annotations

import datetime as dt
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AmoOAuthToken(Base):
    """Один набор токенов (single-tenant)."""

    __tablename__ = "amo_oauth_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {})


class ContactBinding(Base):
    __tablename__ = "contact_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    amo_contact_id: Mapped[int] = mapped_column(Integer, index=True)
    phone_normalized: Mapped[str] = mapped_column(String(32), index=True)
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)


class ProcessedEvent(Base):
    __tablename__ = "processed_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    dedup_key: Mapped[str] = mapped_column(String(512), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("source", "dedup_key", name="uq_dedup"),)


class LiveinformTrackingMap(Base):
    """Запасной маппинг tracking → liveinform_id (если вебхук не отдаёт id)."""

    __tablename__ = "liveinform_tracking_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracking_normalized: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    liveinform_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )
