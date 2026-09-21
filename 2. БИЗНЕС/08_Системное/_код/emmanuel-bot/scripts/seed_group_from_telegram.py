#!/usr/bin/env python3
"""Сиды состава «Копают рвы» и сообщений с хэштегом. Только чтение Telegram."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TG_SELF = ROOT.parent / "tg-self"
SEED = ROOT / "seed"
CHAT_ID = -1002125032114
sys.path.insert(0, str(TG_SELF))
sys.path.insert(0, str(ROOT))

from app.assigned import assigned_hashtag
from app.hashtag import extract_hashtag, looks_like_report, tag_from_telegram
from app.time_utils import MSK, week_start_from_date  # noqa: E402


def _status_name(p) -> str:
    cls = type(p).__name__ if p is not None else "ChannelParticipant"
    mapping = {
        "ChannelParticipant": "member",
        "ChannelParticipantAdmin": "administrator",
        "ChannelParticipantCreator": "creator",
        "ChannelParticipantBanned": "kicked",
        "ChannelParticipantLeft": "left",
        "ChannelParticipantRestricted": "restricted",
        "ChannelParticipantSelf": "member",
    }
    return mapping.get(cls, "member")


async def main() -> int:
    from tg import open_authorized

    client = await open_authorized("self")
    chat = await client.get_entity(CHAT_ID)
    members = []
    async for p in client.iter_participants(chat):
        members.append(
            {
                "tg_user_id": p.id,
                "first_name": getattr(p, "first_name", None),
                "last_name": getattr(p, "last_name", None),
                "username": getattr(p, "username", None),
                "status": _status_name(getattr(p, "participant", None)),
                "is_bot": bool(getattr(p, "bot", False)),
            }
        )

    by_id = {int(m["tg_user_id"]): m for m in members}

    since = datetime.now(timezone.utc) - timedelta(days=21)
    messages = []
    seen = set()
    async for msg in client.iter_messages(chat, limit=800):
        if msg.date < since:
            break
        text = msg.message or ""
        tag = extract_hashtag(text)
        sender_id = msg.sender_id
        if sender_id is None:
            continue
        uid = int(sender_id)
        if not tag:
            if not looks_like_report(text):
                continue
            m = by_id.get(uid) or {}
            tag = assigned_hashtag(uid) or tag_from_telegram(
                username=m.get("username"),
                first_name=m.get("first_name"),
                last_name=m.get("last_name"),
                tg_user_id=uid,
            )
        local = msg.date.astimezone(MSK)
        week_start = week_start_from_date(local.date()).isoformat()
        key = (uid, week_start)
        row = {
            "tg_user_id": uid,
            "msg_id": msg.id,
            "date": msg.date.astimezone(timezone.utc).isoformat(),
            "hashtag": tag,
            "week_start": week_start,
            "source": "group",
        }
        if key in seen:
            # оставляем более позднее; список идёт новые→старые, первое попадание — последнее
            continue
        seen.add(key)
        messages.append(row)

    await client.disconnect()
    SEED.mkdir(exist_ok=True)
    roster = {
        "chat_id": CHAT_ID,
        "title": getattr(chat, "title", None),
        "members": members,
    }
    (SEED / "group_roster.json").write_text(
        json.dumps(roster, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (SEED / "hashtag_reports.json").write_text(
        json.dumps({"messages": messages}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    humans = [m for m in members if not m["is_bot"]]
    print(f"members={len(members)} humans={len(humans)} bots={len(members) - len(humans)}")
    print(f"hashtag_weeks={len(messages)} since={since.date()}")
    by_week: dict[str, int] = {}
    for r in messages:
        by_week[r["week_start"]] = by_week.get(r["week_start"], 0) + 1
    for w, n in sorted(by_week.items()):
        print(f"  week {w}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
