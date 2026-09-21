"""Загрузка сида состава и отчётов из JSON рядом с приложением."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.assigned import all_assigned_hashtags, out_of_scope_ids
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


async def _apply_assigned_flags(session) -> None:
    for uid, tag in all_assigned_hashtags().items():
        row = await session.scalar(select(GroupMember).where(GroupMember.tg_user_id == uid))
        if row is not None:
            row.assigned_hashtag = tag
    for uid in out_of_scope_ids():
        row = await session.scalar(select(GroupMember).where(GroupMember.tg_user_id == uid))
        if row is not None:
            row.in_scope = False


async def load_seed_files() -> None:
    roster = SEED_DIR / "group_roster.json"
    reports = SEED_DIR / "hashtag_reports.json"
    async with get_session_factory()() as session:
        n_existing = await session.scalar(select(func.count()).select_from(GroupMember))
        if roster.is_file() and not n_existing:
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
            log.info("seed roster: %s members (empty db)", len(members))
        elif roster.is_file():
            log.info("seed roster skipped, live members=%s", n_existing)
        await _apply_assigned_flags(session)
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
            log.info("seed group reports: %s rows", len(rows))
        n_m = await session.scalar(select(func.count()).select_from(GroupMember))
        n_s = await session.scalar(select(func.count()).select_from(WeekSubmission))
        await session.commit()
        log.info("seed done members=%s submissions=%s", n_m, n_s)
