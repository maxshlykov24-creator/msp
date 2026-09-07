#!/usr/bin/env python3
"""Хвосты из переписки: неотвеченные входящие и обещания без закрытия."""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DUMP = BASE / "_выгрузки" / "сообщения.jsonl"
CACHE = BASE / "_выгрузки" / "voice_cache.jsonl"

PROMISE = re.compile(
    r"отпишу|пришлю|скину|сделаю|проверю|уточню|подключусь|разберусь|"
    r"постараюсь|напишу|перезвоню|вернусь с|распишу|займусь|поправлю|"
    r"в ближайш|сегодня буду|завтра сделаю|к завтра",
    re.IGNORECASE,
)
CLOSED = re.compile(
    r"готово|сделал|исправил|поправил|отправил|прислал|выкатил|запустил|"
    r"настроил|добавил|проверил|работает|включил|оплатил|решил",
    re.IGNORECASE,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--silent-days", type=int, default=3)
    args = ap.parse_args()

    voice = {}
    if CACHE.exists():
        for line in CACHE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("text"):
                    voice[r["msg_uid"]] = r["text"].strip()

    chats = defaultdict(list)
    for line in DUMP.open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("scope") != "work":
            continue
        if (r.get("date") or "") < args.since:
            continue
        chats[r["chat_id"]].append(r)

    def body(r):
        if r.get("kind") in ("voice", "video_note"):
            return voice.get(r["msg_uid"], "")
        return (r.get("text") or "").strip()

    now = datetime.now(timezone.utc)

    print("### НЕОТВЕЧЕННЫЕ ВХОДЯЩИЕ (последнее слово за клиентом)\n")
    hanging = []
    for rows in chats.values():
        rows.sort(key=lambda r: r["date"])
        last = rows[-1]
        if last.get("direction") != "in":
            continue
        dt = datetime.fromisoformat(last["date"])
        days = (now - dt).days
        if days < 1:
            continue
        text = body(last)
        if not text or len(text) < 12:
            continue
        # сколько подряд входящих в хвосте
        run = 0
        for r in reversed(rows):
            if r.get("direction") == "in":
                run += 1
            else:
                break
        hanging.append((days, last["chat_title"], last["date"][:10], run, text))
    hanging.sort(key=lambda x: -x[0])
    for days, title, date, run, text in hanging:
        print(f"- {days} дн · {title} · {date} · подряд входящих: {run}")
        print(f"  «{text[:220]}»")

    print("\n\n### ОБЕЩАНИЯ БЕЗ ЯВНОГО ЗАКРЫТИЯ\n")
    found = []
    for rows in chats.values():
        rows.sort(key=lambda r: r["date"])
        for i, r in enumerate(rows):
            if r.get("direction") != "out":
                continue
            text = body(r)
            if not text or not PROMISE.search(text):
                continue
            dt = datetime.fromisoformat(r["date"])
            # закрыл ли сам в следующие 20 своих сообщений
            closed = False
            for nxt in rows[i + 1:i + 40]:
                if nxt.get("direction") != "out":
                    continue
                if CLOSED.search(body(nxt) or ""):
                    closed = True
                    break
            if closed:
                continue
            days = (now - dt).days
            found.append((days, r["chat_title"], r["date"][:10], text))
    found.sort(key=lambda x: -x[0])
    seen = set()
    for days, title, date, text in found:
        key = (title, text[:40])
        if key in seen:
            continue
        seen.add(key)
        print(f"- {days} дн · {title} · {date}")
        print(f"  «{text[:200]}»")


if __name__ == "__main__":
    main()
