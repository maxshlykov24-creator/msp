"""ORM-модели hub: inbox, журнал решений, dead-letter, снимки, теги-пары."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB на PostgreSQL, обычный JSON на прочих БД (sqlite в тестах).
JSONType = JSON().with_variant(JSONB, "postgresql")

# BigInteger PK не автоинкрементит на sqlite → там используем Integer.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class InboxEvent(Base):
    """Входящее событие (Tilda/Pleep/Kommo-webhook/scanner). Вебхук пишет сюда и
    сразу отвечает 202; всю работу с Kommo делает worker."""

    __tablename__ = "inbox"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    # идемпотентность: source + внешний id события/лида
    event_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32))          # tilda|pleep|kommo|scanner
    event_type: Mapped[str] = mapped_column(String(64))      # add_lead|update_contact|status_lead|intake...
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Decision(Base):
    """Журнал принятых решений (machine-readable). Пишется и в shadow-режиме."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    inbox_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    shadow: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EntitySnapshot(Base):
    """JSON-снимок сущности перед любым удалением (для отката)."""

    __tablename__ = "entity_snapshots"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16), index=True)  # contact|lead
    entity_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MergeSlot(Base):
    """Пул тегов ручной склейки: mc_NN (контакты) / md_NN (сделки)."""

    __tablename__ = "merge_slots"
    __table_args__ = (UniqueConstraint("kind", "slot_no", name="uq_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)   # contact|deal
    slot_no: Mapped[int] = mapped_column(Integer)
    tag: Mapped[str] = mapped_column(String(16), index=True)   # mc_01 / md_01
    status: Mapped[str] = mapped_column(String(8), default="free", index=True)  # free|used
    pair_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class RolloutState(Base):
    """Состояние авто-раскатки feature-flags (одна строка, id=1)."""

    __tablename__ = "rollout_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(default=False)
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Handoff(Base):
    """Реестр переносов Pipeline → Сборка (won создаёт новую сделку производства).

    unique по source_lead_id — идемпотентность на уровне БД (доп. к event_key
    в inbox): даже при ручном /internal/handoff повторный вызов на ту же
    исходную сделку не создаст вторую сборочную."""

    __tablename__ = "handoffs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source_lead_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    assembly_lead_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int] = mapped_column(BigInteger, index=True)
    shadow: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CallEvent(Base):
    """Звонок с Asterisk. Уникален по uniqueid канала — повторная доставка
    события из диалплана не создаёт второе примечание и вторую задачу."""

    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    uniqueid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    direction: Mapped[str] = mapped_column(String(8), default="in")   # in|out
    did: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    ext: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    disposition: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    recording: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    contact_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    lead_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    note_done: Mapped[bool] = mapped_column(default=False)
    task_done: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AiCall(Base):
    """AI-звонок голосового агента Pleep по сделке.

    unique по lead_id: Salesbot может повторить вебхук, а второй звонок клиенту
    по той же заявке — это уже обзвон, а не ответ на обращение. `scheduled_at`
    заполняется, когда у клиента тихие часы и звонок ждёт утра."""

    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    tz: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # queued → scheduled (ждёт окна) → dialing → done | failed | skipped
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    outcome: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DupManual(Base):
    """Спорная пара, отправленная на ручную native-merge (маркер + тег пары)."""

    __tablename__ = "dup_manual"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)   # contact|deal
    tag: Mapped[str] = mapped_column(String(16), index=True)
    entity_ids: Mapped[list] = mapped_column(JSONType)            # [id, id, ...]
    reason: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
