#!/usr/bin/env python3
"""Выборка реальных пар «входящее → мой ответ» по сценариям.

Нужна для профилей тона: цифры показывают форму, пары показывают голос.
"""

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
DUMP = BASE / "_выгрузки" / "сообщения.jsonl"
CACHE = BASE / "_выгрузки" / "voice_cache.jsonl"
CLOSE = BASE / "_выгрузки" / "close_circle.json"

SCENARIOS = {
    "цена_дорого": r"дорог|скидк|дешевл|бюджет не|цена|стоимость|сколько стоит|прайс",
    "сроки": r"когда будет|сроки|успе[её]м|дедлайн|к какому числу|затянул|когда сдад",
    "отказ_перенос": r"не смогу|не получится|давай перенес|перенести|отменя|в другой раз",
    "проблема_косяк": r"не работает|ошибк|сломал|баг|проблем|упал|не грузит|косяк",
    "готово_итог": r"готово|сделал|выкатил|发|залил|отправил|принято|запустил",
    "задача_исполнителю": r"нужно сделать|сделай|поправ|проверь|займись|задача|до завтра",
    "приветствие_новый": r"^(добрый день|здравствуйте|привет)",
    "деньги_счет": r"счет|счёт|оплат|перевед|реквизит|предоплат|аванс|₽|рублей",
}


def load_close():
    if not CLOSE.exists():
        return set()
    return {int(x) for x in json.loads(CLOSE.read_text(encoding="utf-8"))["chat_ids"]}


def load_voice():
    out = {}
    if CACHE.exists():
        for line in CACHE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("text"):
                    out[r["msg_uid"]] = r["text"].strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--circle", default="work", choices=["work", "close", "personal"])
    ap.add_argument("--scenario", default="")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--min-len", type=int, default=15)
    args = ap.parse_args()

    close_ids = load_close()
    voice = load_voice()
    chats = defaultdict(list)

    for line in DUMP.open(encoding="utf-8"):
        r = json.loads(line)
        if (r.get("date") or "") < args.since:
            continue
        circle = "close" if r["chat_id"] in close_ids else (r.get("scope") or "personal")
        if circle != args.circle:
            continue
        chats[r["chat_id"]].append(r)

    def body(r):
        if r.get("kind") in ("voice", "video_note"):
            return voice.get(r["msg_uid"], "")
        return (r.get("text") or "").strip()

    names = [args.scenario] if args.scenario else list(SCENARIOS)
    for name in names:
        pat = re.compile(SCENARIOS[name], re.IGNORECASE)
        found = []
        for rows in chats.values():
            rows.sort(key=lambda r: r["date"])
            for i, r in enumerate(rows):
                if r.get("direction") != "in":
                    continue
                bin_ = body(r)
                if not bin_ or not pat.search(bin_):
                    continue
                reply = []
                for nxt in rows[i + 1:i + 4]:
                    if nxt.get("direction") != "out":
                        break
                    b = body(nxt)
                    if b:
                        reply.append(b)
                if not reply:
                    continue
                joined = " ⏎ ".join(reply)
                if len(joined) < args.min_len:
                    continue
                found.append((r["chat_title"], r["date"][:10], bin_[:400], joined[:600]))
        random.seed(7)
        random.shuffle(found)
        print(f"\n{'='*70}\n## {name} [{args.circle}] — найдено {len(found)}\n")
        for title, date, q, a in found[:args.limit]:
            print(f"--- {title} · {date}")
            print(f"ВХОД: {q}")
            print(f"ОТВЕТ: {a}\n")


if __name__ == "__main__":
    main()
