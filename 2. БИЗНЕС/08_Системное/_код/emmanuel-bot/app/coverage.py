"""Зачёт отчёта по хэштегу и сводка покрытия состава группы."""

from __future__ import annotations

import html
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assigned import assigned_hashtag, is_out_of_scope
from app.hashtag import (
    DIGEST_HASHTAG,
    HASHTAG_RE,
    extract_hashtag,
    looks_like_report,
    override_from_tag,
    tag_from_telegram,
)
from app.models import DmCoveragePointer, GroupMember, User, WeekSubmission
from app.time_utils import (
    last_sunday_on_or_before,
    late_report_bucket,
    now_msk,
    to_msk,
    week_range_label,
    week_start_from_date,
)

__all__ = [
    "DIGEST_HASHTAG",
    "HASHTAG_RE",
    "extract_hashtag",
    "looks_like_report",
    "override_from_tag",
    "tag_from_telegram",
    "last_sunday_on_or_before",
    "member_label",
    "upsert_member",
    "upsert_submission",
    "latest_hashtag_for_user",
    "identity_hashtag",
    "adopt_hashtag_from_group",
    "load_scope_members",
    "submitted_ids_for_week",
    "submitted_at_for_week",
    "format_coverage_text",
    "format_public_digest",
    "load_dm_pointer",
    "save_dm_pointer",
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
    if is_out_of_scope(tg_user_id):
        in_scope = False
    preset = assigned_hashtag(tg_user_id)
    if row is None:
        row = GroupMember(
            tg_user_id=tg_user_id,
            tg_first_name=first_name,
            tg_last_name=last_name,
            tg_username=username,
            status=status,
            is_bot=is_bot,
            in_scope=in_scope,
            assigned_hashtag=preset,
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
        if preset:
            row.assigned_hashtag = preset
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


async def identity_hashtag(
    session: AsyncSession,
    *,
    tg_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    """Подпись отчёта без # в тексте: назначенный, прошлый из группы, иначе ник Telegram."""
    preset = assigned_hashtag(tg_user_id)
    if preset:
        return preset if preset.startswith("#") else f"#{preset}"
    last = await latest_hashtag_for_user(session, tg_user_id)
    if last:
        return last
    member = await session.scalar(select(GroupMember).where(GroupMember.tg_user_id == tg_user_id))
    if member is not None:
        if (member.assigned_hashtag or "").strip():
            tag = member.assigned_hashtag.strip()
            return tag if tag.startswith("#") else f"#{tag}"
        username = username or member.tg_username
        first_name = first_name or member.tg_first_name
        last_name = last_name or member.tg_last_name
    return tag_from_telegram(
        username=username,
        first_name=first_name,
        last_name=last_name,
        tg_user_id=tg_user_id,
    )


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
    """Имя из группового отчёта или заданное владельцем. True — спрашивать не нужно."""
    if (user.report_hashtag_override or "").strip():
        return True
    tag = await latest_hashtag_for_user(session, user.tg_user_id)
    member = await session.scalar(select(GroupMember).where(GroupMember.tg_user_id == user.tg_user_id))
    if not tag and member is not None and (member.assigned_hashtag or "").strip():
        tag = member.assigned_hashtag
    if not tag:
        preset = assigned_hashtag(user.tg_user_id)
        tag = preset
    if not tag:
        return bool(member is not None and not member.in_scope) or is_out_of_scope(user.tg_user_id)
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


async def submitted_at_for_week(session: AsyncSession, week_start: date) -> dict[int, datetime]:
    rows = (
        await session.scalars(select(WeekSubmission).where(WeekSubmission.week_start == week_start))
    ).all()
    return {int(r.tg_user_id): to_msk(r.submitted_at) for r in rows}


async def load_dm_pointer(session: AsyncSession, tg_user_id: int) -> DmCoveragePointer | None:
    return await session.scalar(select(DmCoveragePointer).where(DmCoveragePointer.tg_user_id == tg_user_id))


async def save_dm_pointer(
    session: AsyncSession,
    *,
    tg_user_id: int,
    message_id: int,
    week_start: date,
) -> None:
    row = await load_dm_pointer(session, tg_user_id)
    now = now_msk()
    if row is None:
        session.add(
            DmCoveragePointer(
                tg_user_id=tg_user_id,
                message_id=message_id,
                week_start=week_start,
                updated_at=now,
            )
        )
    else:
        row.message_id = message_id
        row.week_start = week_start
        row.updated_at = now
    await session.flush()


def coverage_pct(wrote: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(100 * wrote / total)


_BUCKET_TITLES = (
    ("sun", "Написали до вс 23:59"),
    ("mon_am", "Написали в пн до 12:00"),
    ("mon_pm", "Написали в пн после 12:00"),
    ("later", "Написали позже пн"),
)


def _append_member_list(lines: list[str], people: list[GroupMember]) -> None:
    if people:
        for m in sorted(people, key=lambda x: member_label(x).lower()):
            lines.append(f"· {html.escape(member_label(m))}")
    else:
        lines.append("· никого")


def format_coverage_text(
    *,
    week_start: date,
    members: list[GroupMember],
    submitted_ids: set[int],
    sunday: date | None = None,
    submitted_at: dict[int, datetime] | None = None,
) -> str:
    total = len(members)
    wrote = [m for m in members if m.tg_user_id in submitted_ids]
    missing = [m for m in members if m.tg_user_id not in submitted_ids]
    n_ok = len(wrote)
    times = submitted_at or {}
    buckets: dict[str, list[GroupMember]] = {key: [] for key, _ in _BUCKET_TITLES}
    for m in wrote:
        when = times.get(m.tg_user_id)
        bucket = late_report_bucket(when, week_start) if when is not None else "sun"
        buckets[bucket].append(m)
    lines = [
        f"<b>{n_ok} из {total}</b>, {coverage_pct(n_ok, total)}% ({html.escape(week_range_label(week_start))})",
    ]
    for key, title in _BUCKET_TITLES:
        if key == "later" and not buckets[key]:
            continue
        lines += ["", f"<b>{title}</b>"]
        _append_member_list(lines, buckets[key])
    lines += ["", "<b>Не написали</b>"]
    _append_member_list(lines, missing)
    return "\n".join(lines)


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    head = f"{DIGEST_HASHTAG} <b>{wrote} из {total}</b>, {coverage_pct(wrote, total)}% ({week_range_label(week_start)})"
    if total > 0 and wrote >= total:
        return f"{head}\n\nЗа неделю отчёт написали все )"
    return (
        f"{head}\n"
        f"\n"
        f"Не написали {missing}\n"
        f"\n"
        f"Кто ещё не написал, переходите в @emmrov_bot, он поможет собрать текст )"
    )
