#!/usr/bin/env python3
"""Юнит-проверка: отчёт = хэштег."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hashtag import DIGEST_HASHTAG, extract_hashtag, looks_like_report, override_from_tag, tag_from_telegram
from app.assigned import assigned_hashtag, is_out_of_scope
from app.time_utils import last_sunday_on_or_before, week_range_label
from datetime import date


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    pct = 0 if total <= 0 else round(100 * wrote / total)
    head = f"{DIGEST_HASHTAG} <b>{wrote} из {total}</b>, {pct}% ({week_range_label(week_start)})"
    if total > 0 and wrote >= total:
        return f"{head}\n\nЗа неделю отчёт написали все )"
    return (
        f"{head}\n"
        f"\n"
        f"Не написали {missing}\n"
        f"\n"
        f"Кто ещё не написал, переходите в @emmrov_bot, он поможет собрать текст )"
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
    check(extract_hashtag("#рвыотчёт <b>14 из 34</b>") is None, "digest tag skipped")
    check(extract_hashtag("#рвыотчёт #ШлыковМаксим") == "#ШлыковМаксим", "digest then name")
    denis = (
        "1. Сколько времени потратил на личный духовный рост (часы молитвы)?  2\n"
        "2. В чем мы, как команда, можем тебе помочь?"
    )
    andrey = "1.Сколько времени потратили на личный духовный рост (часы молитвы)? 4  2.В чем мы как команда можем тебе помочь?"
    check(looks_like_report(denis), "denis template")
    check(looks_like_report(andrey), "andrey template")
    check(not looks_like_report("1. Сколько времени потратил на личный духовный рост (часы молитвы)? 4"), "one item not enough")
    check(not looks_like_report("1. Купил хлеб 2. Забрал детей"), "numbered chores not report")
    check(not looks_like_report("Мужчины, давайте дальше отчеты писать, понимаю, что много дел"), "nudge not report")
    check(not looks_like_report("🟢Уже завтра ночная мужская молитва! 20:00 - 22:00"), "event not report")
    check(tag_from_telegram(username="Andreynkl", first_name="Андрей", last_name=None, tg_user_id=1) == "#Andreynkl", "nick username")
    check(tag_from_telegram(username=None, first_name="Денис", last_name="Петрий", tg_user_id=2) == "#ПетрийДенис", "nick name")
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
    check(week_range_label(date(2026, 9, 14)) == "14-20 сентября", "week range same month")
    check(week_range_label(date(2026, 9, 28)) == "28 сентября - 4 октября", "week range cross month")
    t = format_public_digest(week_start=date(2026, 9, 14), total=35, wrote=18)
    check(t.startswith("#рвыотчёт <b>18 из 35</b>, 51% (14-20 сентября)"), "public head")
    check("Не написали 17" in t and "@emmrov_bot" in t, "public digest")
    check("?" not in t, "public no question")
    t_all = format_public_digest(week_start=date(2026, 9, 14), total=34, wrote=34)
    check(t_all.startswith("#рвыотчёт <b>34 из 34</b>, 100% (14-20 сентября)"), "public all")
    t16 = format_public_digest(week_start=date(2026, 9, 14), total=34, wrote=16)
    check(t16.startswith("#рвыотчёт <b>16 из 34</b>, 47% (14-20 сентября)"), "public 16 of 34")
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
