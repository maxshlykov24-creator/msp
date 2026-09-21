#!/usr/bin/env python3
"""Юнит-проверка: отчёт = хэштег."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hashtag import extract_hashtag, override_from_tag
from app.assigned import assigned_hashtag, is_out_of_scope
from app.time_utils import last_sunday_on_or_before
from datetime import date


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    if total > 0 and wrote >= total:
        return f"За неделю отчёт написали все {total}."
    return (
        f"За неделю отчёт написали {wrote} из {total}. Не написали {missing}.\n"
        f"\n"
        f"Не успел? Напиши @emmrov_bot, он поможет собрать отчёт."
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
    check(override_from_tag("#ШлыковМаксим") == "ШлыковМаксим", "override strip")
    check(override_from_tag("ШлыковМаксим") == "ШлыковМаксим", "override plain")
    check(assigned_hashtag(422813303) == "#Арам", "assigned aram")
    check(assigned_hashtag(924861280) == "#МаксимБ", "assigned maxim b")
    check(is_out_of_scope(291663131), "pastor out of scope")
    check(not is_out_of_scope(435207481), "max in scope")
    check(last_sunday_on_or_before(date(2026, 9, 21)) == date(2026, 9, 20), "last sunday mon")
    check(last_sunday_on_or_before(date(2026, 9, 20)) == date(2026, 9, 20), "last sunday sun")
    t = format_public_digest(week_start=date(2026, 9, 14), total=35, wrote=18)
    check("18 из 35" in t and "Не написали 17" in t, "public digest")
    check("@emmrov_bot" in t and "2026-09-14" not in t, "public no date")
    t_all = format_public_digest(week_start=date(2026, 9, 14), total=34, wrote=34)
    check(t_all == "За неделю отчёт написали все 34.", "public all")
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
