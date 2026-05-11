from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProcessedEventStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    bonustransaction_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bonus_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cashable_total_rub: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ProcessedEventStatus.active.value, index=True)
    # Manual SPEND state (idempotent):
    spent_amount: Mapped[int] = mapped_column(Integer, default=0)
    last_spend_intent: Mapped[int] = mapped_column(Integer, default=0)
    spend_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    spend_bonustransaction_ids: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    # Наша надбавка к discount каждой позиции заказа (для применения скидки за списание бонусов).
    # Ключ — id позиции в МС, значение — добавленный нами процент (0..100).
    spend_extra_discounts: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Последние записанные нами в МС значения attrs (для детекции «менеджер тронул»):
    last_synced_attrs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LoyaltyMember(Base):
    """Локальная копия данных уровня для планировщика (источник правды для ежедневных проверок)."""

    __tablename__ = "loyalty_members"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tier: Mapped[int] = mapped_column(Integer, default=0)  # 0 знакомство, 1 дружба, 2 любовь
    annual_sum_rub: Mapped[int] = mapped_column(BigInteger, default=0)
    enrolled_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_tier_review_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    tier_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # «Пол» уровня после ручных правок менеджером (auto-resync не опускает ниже этого).
    tier_floor: Mapped[int] = mapped_column(Integer, default=0)
    # Клиент заблокирован для ПЛ (нет начислений / списаний).
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # День рождения: для job birthday_bonus (ежегодно). Заполняйте импортом или sync из МС.
    birth_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    birth_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_birthday_bonus_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Последние записанные нами значения attributes контрагента (для детекции ручных правок):
    last_synced_cp_attrs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BonusBatch(Base):
    __tablename__ = "bonus_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    original_amount: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[date] = mapped_column(Date, index=True)
    # Момент активации батча: если now() < activates_at — батч в «Ожидают активации».
    # NULL → активен сразу (legacy).
    activates_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    source_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    bonustransaction_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    batch_type: Mapped[str] = mapped_column(String(32), default="earn")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BonusLogAction(str, enum.Enum):
    EARN = "EARN"
    SPEND = "SPEND"
    SPEND_REFUND = "SPEND_REFUND"   # ручной отзыв списания (delta < 0 в apply_spend_intent)
    CANCEL = "CANCEL"
    RETURN_EARN = "RETURN_EARN"
    RETURN_SPEND = "RETURN_SPEND"
    TIER_UP = "TIER_UP"
    TIER_DOWN = "TIER_DOWN"
    MANUAL_TIER_UP = "MANUAL_TIER_UP"
    REVERT_TIER_DOWN = "REVERT_TIER_DOWN"
    BLOCK = "BLOCK"
    UNBLOCK = "UNBLOCK"
    REVERT_BALANCE_FIELD = "REVERT_BALANCE_FIELD"
    EXPIRE = "EXPIRE"
    BIRTHDAY = "BIRTHDAY"
    WELCOME = "WELCOME"
    MANUAL_SYNC = "MANUAL_SYNC"
    RECONCILIATION = "RECONCILIATION"


class BonusLog(Base):
    __tablename__ = "bonus_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    customerorder_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    customerorder_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    tier_at_moment: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cashback_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    order_sum_kop: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bonus_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bonustransaction_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
