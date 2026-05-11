from __future__ import annotations

import datetime as dt
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint
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


class AmojoChannelState(Base):
    __tablename__ = "amojo_channel_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    scope_id: Mapped[str] = mapped_column(String(256), default="")
    amojo_account_id: Mapped[str] = mapped_column(String(64), default="")


class ConversationMap(Base):
    """
    Стабильный conversation_id в amo = наш ключ (например tm-{dialog}).
    """

    __tablename__ = "conversation_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_id: Mapped[str] = mapped_column(String(256), index=True)
    external_conversation_id: Mapped[str] = mapped_column(String(512), index=True)
    talkme_client_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    # Последний активный dialogId Talk-me (может меняться при возобновлении диалога клиентом).
    talkme_dialog_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # JSON с последним известным payload Talk-me
    talkme_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {})

    __table_args__ = (UniqueConstraint("scope_id", "external_conversation_id", name="uq_conv_scope"),)


class ProcessedEvent(Base):
    __tablename__ = "processed_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # talkme / amojo
    dedup_key: Mapped[str] = mapped_column(String(256), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("source", "dedup_key", name="uq_dedup"),)


class KvState(Base):
    """Простой key-value стейт (last_seen_ts поллера и т.п.)."""

    __tablename__ = "kv_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(timezone.utc)
    )
