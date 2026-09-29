"""Задачи на переход этапа. Чистое расписание, без amo.

Один вебхук на все этапы: срок считается здесь, amo шлёт pipeline_id и status_id.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import lib

TZ = ZoneInfo("Europe/Moscow")
WORK_FROM = 11
WORK_TO = 21


def day_word(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def snap(dt: datetime) -> datetime:
    d = dt.astimezone(TZ)
    if d.hour < WORK_FROM:
        return d.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)
    if d.hour > WORK_TO or (d.hour == WORK_TO and (d.minute or d.second)):
        nxt = d + timedelta(days=1)
        return nxt.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)
    return d.replace(microsecond=0)


def shift(dt: datetime, hours: int = 0, days: int = 0) -> datetime:
    return snap(dt.astimezone(TZ) + timedelta(hours=hours, days=days))


def morning(dt: datetime, days: int) -> datetime:
    d = dt.astimezone(TZ) + timedelta(days=days)
    return d.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)


def end_of_shift(dt: datetime) -> datetime:
    d = snap(dt)
    return d.replace(hour=WORK_TO, minute=0, second=0, microsecond=0)


def _dedupe(jobs: list[tuple[str, datetime, int | None]]) -> list[tuple[str, datetime, int | None]]:
    seen: set[tuple[str, int, int | None]] = set()
    out = []
    for text, due, who in jobs:
        key = (text, int(due.timestamp()) // 60, who)
        if key in seen:
            continue
        seen.add(key)
        out.append((text, due, who))
    return out


def _sequence(jobs: list[tuple[str, datetime, int | None]]) -> list[tuple[str, datetime, int | None]]:
    """Следующий шаг строго позже предыдущего. Иначе это вторая задача в ту же минуту."""
    out = []
    last = -1
    for text, due, who in jobs:
        minute = int(due.timestamp()) // 60
        if minute <= last:
            continue
        last = minute
        out.append((text, due, who))
    return out


def follow_mode(pipeline_id: int, status_id: int) -> str:
    """stack: просроченная остаётся открытой и уходит на Максима, новая с тем же дедлайном.

    keep: просроченную не трогаем, следующая встаёт своим будущим сроком.
    handoff: просроченную закрываем на Максима, у менеджера остаётся одна новая.
    """
    if pipeline_id == lib.PIPELINE_MKT_NEW and status_id == lib.ST["mkt_talk"]:
        return "stack"
    if pipeline_id != lib.PIPELINE_SALES_NEW:
        return "replace"
    if status_id == lib.ST["new"]:
        return "stack"
    if status_id == lib.ST["waitlist"]:
        return "keep"
    if status_id in (lib.ST["in_work"], lib.ST["pay_wait"], lib.ST["prod"]):
        return "handoff"
    return "replace"


def jobs(pipeline_id: int, status_id: int, moment: datetime) -> list[tuple[str, datetime, int | None]]:
    """Текст, срок, ответственный. None = текущий по сделке."""
    n = moment.astimezone(TZ)
    if pipeline_id == lib.PIPELINE_MKT_NEW and status_id == lib.ST["mkt_talk"]:
        return [
            ("Взять в работу", snap(n), lib.USER_POLINA),
            ("Ждёт 30 минут", snap(n + timedelta(minutes=30)), lib.USER_POLINA),
        ]
    if pipeline_id != lib.PIPELINE_SALES_NEW:
        return []
    if status_id == lib.ST["new"]:
        return [
            ("Взять заявку", snap(n), None),
            ("Ждёт 30 минут", snap(n + timedelta(minutes=30)), None),
            ("Ждёт 2 часа", shift(n, hours=2), lib.USER_OKSANA),
        ]
    if status_id == lib.ST["in_work"]:
        first = shift(n, hours=3)
        second = shift(n, hours=6)
        return _sequence([
            ("Молчит 3 часа", first, None),
            ("Молчит 6 часов", second, None),
            ("Сутки молчит, в отмену", shift(n, days=1), None),
        ])
    if status_id == lib.ST["waitlist"]:
        return _sequence([
            (f"Пришла ли поставка, {days} {day_word(days)}", morning(n, days), None)
            for days in (4, 8, 12, 16, 20)
        ])
    if status_id == lib.ST["pay_wait"]:
        return _sequence([
            ("Написать про оплату, 1 час", shift(n, hours=1), None),
            ("Написать про оплату, прошло 4 часа", shift(n, hours=4), None),
            ("Написать про оплату, прошли сутки", shift(n, days=1), None),
        ])
    if status_id == lib.ST["paid"]:
        return [("Производство или сборка, 1 час", shift(n, hours=1), None)]
    if status_id == lib.ST["prod"]:
        return _sequence([
            ("Проверить готовность", snap(n), None),
            ("Проверить готовность, прошло 4 дня", morning(n, 4), None),
        ])
    if status_id == lib.ST["pack"]:
        return [("Собрать и отправить", end_of_shift(n), None)]
    if status_id == lib.ST["sent"]:
        return [("Проставить трек-номер", snap(n), None)]
    if status_id == lib.ST["won"]:
        return [("Взять обратную связь, 2 дня", morning(n, 2), None)]
    return []
