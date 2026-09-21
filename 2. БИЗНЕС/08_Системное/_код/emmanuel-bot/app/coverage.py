"""Зачёт отчёта по хэштегу и сводка покрытия состава группы."""

from __future__ import annotations

import html
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hashtag import HASHTAG_RE, extract_hashtag, override_from_tag
from app.models import GroupMember, User, WeekSubmission
from app.time_utils import last_sunday_on_or_before, now_msk, to_msk, week_start_from_date

__all__ = [
    "HASHTAG_RE",
    "extract_hashtag",
    "override_from_tag",
    "last_sunday_on_or_before",
    "member_label",
    "upsert_member",
    "upsert_submission",
    "latest_hashtag_for_user",
    "adopt_hashtag_from_group",
    "load_scope_members",
    "submitted_ids_for_week",
    "format_coverage_text",
    "format_public_digest",
]


def member_label(m: GroupMember) -> str:
    name = " ".join(p for p in ((m.tg_first_name or "").strip(), (m.tg_last_name or "").strip()) if p)
    if m.tg_username:
        handle = f"@{m.tg_username}"
        return f"{name} ({handle})" if name else handle
    return name or str(m.tg_user_id)


async def upsert_member(
    session: AsyncSession,
    *,
    tg_user_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
    status: str,
    is_bot: bool,
) -> GroupMember:
    row = await session.scalar(select(GroupMember).where(GroupMember.tg_user_id == tg_user_id))
    now = now_msk()
    in_scope = (not is_bot) and status in ("member", "administrator", "creator", "restricted")
    if row is None:
        row = GroupMember(
            tg_user_id=tg_user_id,
            tg_first_name=first_name,
            tg_last_name=last_name,
            tg_username=username,
            status=status,
            is_bot=is_bot,
            in_scope=in_scope,
            updated_at=now,
        )
        session.add(row)
    else:
        row.tg_first_name = first_name
        row.tg_last_name = last_name
        row.tg_username = username
        row.status = status
        row.is_bot = is_bot
        row.in_scope = in_scope
        row.updated_at = now
    await session.flush()
    return row


async def upsert_submission(
    session: AsyncSession,
    *,
    tg_user_id: int,
    week_start: date,
    source: str,
    msg_id: int | None,
    hashtag: str,
    submitted_at: datetime,
) -> WeekSubmission:
    row = await session.scalar(
        select(WeekSubmission).where(
            WeekSubmission.tg_user_id == tg_user_id,
            WeekSubmission.week_start == week_start,
        )
    )
    submitted_at = to_msk(submitted_at)
    if row is None:
        row = WeekSubmission(
            tg_user_id=tg_user_id,
            week_start=week_start,
            source=source,
            msg_id=msg_id,
            hashtag=hashtag,
            submitted_at=submitted_at,
        )
        session.add(row)
    else:
        if submitted_at >= to_msk(row.submitted_at):
            row.source = source
            row.msg_id = msg_id
            row.hashtag = hashtag
            row.submitted_at = submitted_at
    await session.flush()
    return row


async def latest_hashtag_for_user(session: AsyncSession, tg_user_id: int) -> str | None:
    """Последний хэштег, которым человек подписывал отчёт в группе."""
    row = await session.scalar(
        select(WeekSubmission)
        .where(WeekSubmission.tg_user_id == tg_user_id)
        .order_by(WeekSubmission.submitted_at.desc(), WeekSubmission.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    tag = (row.hashtag or "").strip()
    return tag or None


async def adopt_hashtag_from_group(session: AsyncSession, user: User) -> bool:
    """Имя из группового отчёта. True — спрашивать не нужно."""
    if (user.report_hashtag_override or "").strip():
        return True
    tag = await latest_hashtag_for_user(session, user.tg_user_id)
    if not tag:
        return False
    user.report_hashtag_override = override_from_tag(tag)
    return True


async def load_scope_members(session: AsyncSession) -> list[GroupMember]:
    rows = (
        await session.scalars(
            select(GroupMember)
            .where(GroupMember.in_scope.is_(True), GroupMember.is_bot.is_(False))
            .order_by(GroupMember.tg_first_name)
        )
    ).all()
    return list(rows)


async def submitted_ids_for_week(session: AsyncSession, week_start: date) -> set[int]:
    rows = (
        await session.scalars(select(WeekSubmission.tg_user_id).where(WeekSubmission.week_start == week_start))
    ).all()
    return set(rows)


def format_coverage_text(
    *,
    week_start: date,
    members: list[GroupMember],
    submitted_ids: set[int],
    sunday: date | None = None,
) -> str:
    total = len(members)
    wrote = [m for m in members if m.tg_user_id in submitted_ids]
    missing = [m for m in members if m.tg_user_id not in submitted_ids]
    n_ok = len(wrote)
    n_no = len(missing)
    header = f"Неделя с {week_start.isoformat()}"
    if sunday:
        header += f", сверка за воскресенье {sunday.isoformat()}"
    lines = [
        f"<b>{html.escape(header)}</b>",
        "",
        f"Написали (есть хэштег): <b>{n_ok}</b> из {total}",
        f"Не написали: <b>{n_no}</b>",
        "",
        "<b>Написали</b>",
    ]
    if wrote:
        for m in sorted(wrote, key=lambda x: member_label(x).lower()):
            lines.append(f"· {html.escape(member_label(m))}")
    else:
        lines.append("· никого")
    lines += ["", "<b>Не написали</b>"]
    if missing:
        for m in sorted(missing, key=lambda x: member_label(x).lower()):
            lines.append(f"· {html.escape(member_label(m))}")
    else:
        lines.append("· никого")
    return "\n".join(lines)


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    if total > 0 and wrote >= total:
        return f"На неделе с {week_start.isoformat()} отчёт написали все {total}."
    return (
        f"На этой неделе (с {week_start.isoformat()}) отчёт написали {wrote} из {total}. "
        f"Не написали {missing}."
    )
