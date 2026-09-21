"""Загрузка сида состава и хэштег-отчётов из JSON рядом с приложением."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.coverage import upsert_member, upsert_submission
from app.database import get_session_factory
from app.models import GroupMember, WeekSubmission
from app.time_utils import MSK

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def _parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt


async def load_seed_files() -> None:
    roster = SEED_DIR / "group_roster.json"
    reports = SEED_DIR / "hashtag_reports.json"
    async with get_session_factory()() as session:
        if roster.is_file():
            data = json.loads(roster.read_text(encoding="utf-8"))
            members = data.get("members") or []
            for m in members:
                await upsert_member(
                    session,
                    tg_user_id=int(m["tg_user_id"]),
                    first_name=m.get("first_name"),
                    last_name=m.get("last_name"),
                    username=m.get("username"),
                    status=m.get("status") or "member",
                    is_bot=bool(m.get("is_bot")),
                )
            log.info("seed roster: %s members", len(members))
        if reports.is_file():
            data = json.loads(reports.read_text(encoding="utf-8"))
            rows = data.get("messages") or []
            for r in rows:
                await upsert_submission(
                    session,
                    tg_user_id=int(r["tg_user_id"]),
                    week_start=date.fromisoformat(r["week_start"]),
                    source=r.get("source") or "group",
                    msg_id=r.get("msg_id"),
                    hashtag=r["hashtag"],
                    submitted_at=_parse_dt(r["date"]),
                )
            log.info("seed hashtag reports: %s rows", len(rows))
        n_m = await session.scalar(select(GroupMember).limit(1))
        n_s = await session.scalar(select(WeekSubmission).limit(1))
        await session.commit()
        log.info("seed done members_present=%s submissions_present=%s", bool(n_m), bool(n_s))
