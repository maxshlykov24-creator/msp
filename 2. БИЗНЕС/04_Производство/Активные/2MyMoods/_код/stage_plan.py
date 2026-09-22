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


def jobs(pipeline_id: int, status_id: int, moment: datetime) -> list[tuple[str, datetime, int | None]]:
    """Текст, срок, ответственный. None = текущий по сделке."""
    n = moment.astimezone(TZ)
    if pipeline_id == lib.PIPELINE_MKT_NEW and status_id == lib.ST["mkt_talk"]:
        return [
            ("Взять в работу", snap(n), lib.USER_POLINA),
            ("Повтор: 30 минут", snap(n + timedelta(minutes=30)), lib.USER_POLINA),
        ]
    if pipeline_id != lib.PIPELINE_SALES_NEW:
        return []
    if status_id == lib.ST["new"]:
        return _dedupe([
            ("Взять заявку", snap(n), None),
            ("Повтор: заявка ещё новая", snap(n + timedelta(minutes=30)), None),
            ("Заявку не взяли", shift(n, hours=2), lib.USER_OKSANA),
        ])
    if status_id == lib.ST["in_work"]:
        first = shift(n, hours=3)
        second = shift(n, hours=6)
        rows = [
            ("Связаться с клиентом", snap(n), None),
            ("Повтор, если молчит", first, None),
        ]
        if int(second.timestamp()) // 60 != int(first.timestamp()) // 60:
            rows.append(("Повтор, если молчит, ещё раз", second, None))
        rows.append(("Сутки на этапе: в отмену с причиной", shift(n, days=1), None))
        return _dedupe(rows)
    if status_id == lib.ST["waitlist"]:
        return [
            (f"Пришла ли поставка, через {days} {day_word(days)}", morning(n, days), None)
            for days in (4, 8, 12, 16, 20)
        ]
    if status_id == lib.ST["pay_wait"]:
        return _dedupe([
            ("Написать про оплату", shift(n, hours=1), None),
            ("Написать про оплату ещё раз", shift(n, hours=4), None),
            ("Написать про оплату, крайний срок", shift(n, days=1), None),
        ])
    if status_id == lib.ST["paid"]:
        return [("Производство или сборка", shift(n, hours=1), None)]
    if status_id == lib.ST["prod"]:
        return [
            ("Проверить готовность", snap(n), None),
            ("Проверить готовность", snap(n), lib.USER_OKSANA),
            ("Проверить готовность, через 4 дня", morning(n, 4), None),
            ("Проверить готовность, через 4 дня", morning(n, 4), lib.USER_OKSANA),
        ]
    if status_id == lib.ST["pack"]:
        return [("Собрать и отправить", end_of_shift(n), None)]
    if status_id == lib.ST["sent"]:
        return [("Проставить трек-номер", snap(n), None)]
    if status_id == lib.ST["won"]:
        return [("Взять обратную связь", morning(n, 2), None)]
    return []
