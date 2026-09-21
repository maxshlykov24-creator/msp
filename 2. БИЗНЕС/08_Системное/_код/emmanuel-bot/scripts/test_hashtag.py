#!/usr/bin/env python3
"""Юнит-проверка: отчёт = хэштег."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hashtag import extract_hashtag
from app.time_utils import last_sunday_on_or_before
from datetime import date


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    if total > 0 and wrote >= total:
        return f"На неделе с {week_start.isoformat()} отчёт написали все {total}."
    return (
        f"На этой неделе (с {week_start.isoformat()}) отчёт написали {wrote} из {total}. "
        f"Не написали {missing}."
    )


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print("ok", msg)


def main() -> int:
    check(extract_hashtag("#СапелкинСергей\n1. молитва") == "#СапелкинСергей", "plain tag")
    check(extract_hashtag("привет аминь") is None, "no tag")
    check(extract_hashtag("🔥 круто") is None, "emoji comment")
    check(extract_hashtag("1. Сколько времени\n2") is None, "report without tag")
    check(extract_hashtag("#Кирилл Спиридонов") == "#Кирилл", "tag then space")
    check(extract_hashtag("текст #ПоляковМаксим ещё") == "#ПоляковМаксим", "tag mid text")
    check(last_sunday_on_or_before(date(2026, 9, 21)) == date(2026, 9, 20), "last sunday mon")
    check(last_sunday_on_or_before(date(2026, 9, 20)) == date(2026, 9, 20), "last sunday sun")
    t = format_public_digest(week_start=date(2026, 9, 14), total=35, wrote=18)
    check("18 из 35" in t and "Не написали 17" in t, "public digest")
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
