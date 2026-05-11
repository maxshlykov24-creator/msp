from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    tg_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    report_hashtag_override: Mapped[str | None] = mapped_column(String(120), nullable=True)

    streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_report_week_start: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_bot_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    revelations: Mapped[list["Revelation"]] = relationship(back_populates="user")
    archived: Mapped[list["RevelationArchive"]] = relationship(back_populates="user")
    reports: Mapped[list["Report"]] = relationship(back_populates="user")

    def report_hashtag(self) -> str:
        if self.report_hashtag_override and self.report_hashtag_override.strip():
            h = self.report_hashtag_override.strip()
            return h if h.startswith("#") else f"#{h}"
        ln = (self.tg_last_name or "").strip()
        fn = (self.tg_first_name or "").strip()
        tag = f"{ln}{fn}".replace(" ", "")
        if not tag and self.tg_username:
            tag = self.tg_username.strip()
        if not tag:
            tag = str(self.tg_user_id)
        return f"#{tag}"


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_report_user_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    week_start: Mapped[date] = mapped_column(Date, index=True)

    q1_prayer_hours: Mapped[str] = mapped_column(String(32))
    q2_sermons: Mapped[str] = mapped_column(String(32))
    q3_fasted: Mapped[bool] = mapped_column(Boolean)
    q4_help: Mapped[str] = mapped_column(Text)
    q5_revelations: Mapped[str] = mapped_column(Text)
    q6_meetings: Mapped[str] = mapped_column(String(32))
    q7_plan_pct: Mapped[str] = mapped_column(String(32))
    q8_next_plan: Mapped[str] = mapped_column(Text)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    on_time: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="reports")


class Revelation(Base):
    __tablename__ = "revelations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="revelations")


class RevelationArchive(Base):
    __tablename__ = "revelation_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    report_week_start: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="archived")
