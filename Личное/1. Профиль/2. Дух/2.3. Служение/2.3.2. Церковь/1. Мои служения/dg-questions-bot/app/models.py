from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GameSession(Base):
    """One meeting session linking a moderator phone and a participant (table screen) phone."""

    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), unique=True, index=True)

    moderator_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    participant_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    # deck / preset choices made by moderator
    deck_continue: Mapped[bool] = mapped_column(Boolean, default=True)
    preset: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "meeting1" | "full"

    # runtime state
    phase: Mapped[int] = mapped_column(Integer, default=1)
    current_question_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replace_used: Mapped[bool] = mapped_column(Boolean, default=False)

    # status flow: waiting_participant → deck_choice → preset_choice → active → finished
    status: Mapped[str] = mapped_column(String(24), default="waiting_participant")

    # message IDs for in-place editing
    mod_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SessionQuestion(Base):
    """One question slot inside a session queue."""

    __tablename__ = "session_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id"), index=True)
    question_number: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer)
    phase_label: Mapped[int] = mapped_column(Integer, default=1)  # 1=лёгкий 2=тепло 3=чуть теплее

    # status: pending → shown → done | skipped | replaced(→ pending again at new order_index)
    status: Mapped[str] = mapped_column(String(16), default="pending")

    shown_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeckLedger(Base):
    """Cross-meeting memory: tracks every question that was played to prevent repetition."""

    __tablename__ = "deck_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deck_id: Mapped[str] = mapped_column(String(32), default="main", index=True)
    question_number: Mapped[int] = mapped_column(Integer)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
