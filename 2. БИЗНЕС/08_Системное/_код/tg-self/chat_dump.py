#!/usr/bin/env python3
"""Читаемая расшифровка чата: кто, когда, что. Для сборки карточек знаний."""

import argparse
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
DUMP = BASE / "_выгрузки" / "сообщения.jsonl"
CACHE = BASE / "_выгрузки" / "voice_cache.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="подстрока названия чата")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    voice = {}
    if CACHE.exists():
        for line in CACHE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("text"):
                    voice[r["msg_uid"]] = r["text"].strip()

    pat = re.compile(args.title, re.IGNORECASE)
    rows = []
    for line in DUMP.open(encoding="utf-8"):
        r = json.loads(line)
        if (r.get("date") or "") < args.since:
            continue
        if not pat.search(r.get("chat_title") or ""):
            continue
        rows.append(r)
    rows.sort(key=lambda r: r["date"])

    out = []
    day = ""
    for r in rows:
        d = r["date"][:10]
        if d != day:
            day = d
            out.append(f"\n=== {d} ===")
        who = "Я" if r.get("direction") == "out" else (r.get("sender_name") or "?")
        kind = r.get("kind")
        if kind in ("voice", "video_note"):
            t = voice.get(r["msg_uid"])
            t = f"[голос] {t}" if t else f"[голос {r.get('duration_sec')}с, не расшифрован]"
        else:
            t = (r.get("text") or "").strip()
            if not t:
                t = f"[{kind}]"
        out.append(f"{r['date'][11:16]} {who}: {t}")

    text = "\n".join(out)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(rows)} сообщений → {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
