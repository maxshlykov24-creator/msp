#!/usr/bin/env python3
"""Одноразовый глубокий дамп LicenseBridge: 10 дней, все чаты + алерты."""

import asyncio
import datetime as dt

from telethon import TelegramClient

import tg

CHATS = [
    ("Интеграции License Bridge", -1003971287479, 10),
    ("Интеграции License Bridge (2)", -5466894944, 10),
    ("LicenseBridge · Алерты", -1004319953663, 4),
    ("Paul (личка)", 6455963634, 10),
    ("Pavel Schmidt (личка)", 389170237, 10),
    ("License Bridge USA", 7415839050, 10),
]


async def dump(client, me, title, cid, days):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    try:
        entity = await client.get_entity(cid)
    except Exception as exc:  # noqa: BLE001
        print(f"\n### {title} ({cid}) — ошибка: {exc}")
        return
    print(f"\n\n===== {title} ({cid}), {days} дн. =====")
    rows = []
    async for msg in client.iter_messages(entity, limit=500):
        if msg.date < since:
            break
        who = "?"
        if msg.sender_id == me.id:
            who = "Я"
        else:
            try:
                s = await msg.get_sender()
                who = getattr(s, "first_name", None) or getattr(s, "title", None) or str(msg.sender_id)
            except Exception:  # noqa: BLE001
                who = str(msg.sender_id)
        text = (msg.text or "").strip()
        if not text and msg.media:
            text = f"[медиа: {type(msg.media).__name__}]"
        rows.append(f"[{msg.date.astimezone().strftime('%d.%m %H:%M')}] #{msg.id} {who}: {text}")
    for line in reversed(rows):
        print(line)
    if not rows:
        print("(пусто)")


async def main():
    api_id, api_hash = tg.credentials()
    client = TelegramClient(str(tg.ACCOUNTS["self"]), api_id, api_hash)
    await client.start()
    me = await client.get_me()
    for title, cid, days in CHATS:
        await dump(client, me, title, cid, days)
    await client.disconnect()


asyncio.run(main())
