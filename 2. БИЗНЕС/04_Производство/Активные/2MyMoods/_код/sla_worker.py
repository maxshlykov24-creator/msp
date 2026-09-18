#!/usr/bin/env python3
"""SLA-задачи только на новых воронках 2MY. Старую не трогает.

Публичное API amo не ставит цифровую воронку. Этот воркер — рабочий контур:
задача на этапе, повтор по правке 17.09, Оксане через 2 ч с новой заявки.
По умолчанию сухой прогон.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import lib

TZ = ZoneInfo("Europe/Moscow")
WORK_FROM = 11
WORK_TO = 21


def now() -> datetime:
    return datetime.now(TZ)


def next_work(dt: datetime) -> datetime:
    d = dt.astimezone(TZ)
    if d.hour < WORK_FROM:
        return d.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)
    if d.hour >= WORK_TO:
        nxt = d + timedelta(days=1)
        return nxt.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)
    return d


def unix(dt: datetime) -> int:
    return int(dt.timestamp())


def open_tasks(amo: lib.Amo, lead_id: int) -> list[dict]:
    st, b = amo.req("GET", f"/api/v4/tasks?filter[entity_id]={lead_id}&filter[is_completed]=0&limit=50")
    if not (200 <= st < 300):
        return []
    return (b.get("_embedded") or {}).get("tasks") or []


def has_text(tasks: list[dict], needle: str) -> bool:
    n = needle.lower()
    return any(n in str(t.get("text") or "").lower() for t in tasks)


def add_task(amo: lib.Amo, lead: dict, text: str, due: datetime, responsible: int | None, apply: bool) -> None:
    rid = responsible or lead.get("responsible_user_id") or lib.USER_OKSANA
    payload = {
        "task_type_id": 1,
        "text": text,
        "complete_till": unix(next_work(due)),
        "entity_id": lead["id"],
        "entity_type": "leads",
        "responsible_user_id": rid,
    }
    if not apply:
        print(f"  dry {lead['id']}: {text} till {next_work(due):%d.%m %H:%M}")
        return
    st, b = amo.req("POST", "/api/v4/tasks", [payload])
    print(f"  + {lead['id']} [{st}] {text}")


def age_hours(lead: dict) -> float:
    ts = lead.get("updated_at") or lead.get("created_at") or 0
    if not ts:
        return 0
    return (time.time() - int(ts)) / 3600


def created_hours(lead: dict) -> float:
    ts = lead.get("created_at") or 0
    return (time.time() - int(ts)) / 3600 if ts else 0


def process_sales(amo: lib.Amo, lead: dict, apply: bool) -> None:
    sid = lead.get("status_id")
    tasks = open_tasks(amo, lead["id"])
    n = now()
    if sid == lib.ST["new"]:
        if not tasks:
            add_task(amo, lead, "Взять заявку в работу", n, None, apply)
        hours = created_hours(lead)
        if hours >= 0.5 and not has_text(tasks, "повтор 30"):
            add_task(amo, lead, "Повтор 30 мин: заявка всё ещё новая", n, None, apply)
        if hours >= 2 and not has_text(tasks, "оксане"):
            add_task(amo, lead, "Эскалация Оксане: новую заявку не взяли 2 часа", n, lib.USER_OKSANA, apply)
        return
    if sid == lib.ST["in_work"]:
        if not has_text(tasks, "каждые 3 часа") and not tasks:
            add_task(amo, lead, "Связаться. Повтор каждые 3 часа", n + timedelta(hours=3), None, apply)
        if created_hours(lead) >= 24 and not has_text(tasks, "24 часа"):
            add_task(amo, lead, "24 часа без ответа: написать ещё раз или в отмену с причиной", n, None, apply)
        return
    if sid == lib.ST["waitlist"]:
        if not has_text(tasks, "поставка"):
            add_task(amo, lead, "Лист ожидания: пришла ли поставка раньше", n + timedelta(days=4), None, apply)
        return
    if sid == lib.ST["visit"]:
        return
    if sid == lib.ST["pay_wait"]:
        if not tasks:
            add_task(amo, lead, "Написать про оплату, не шаблон", n + timedelta(hours=1), None, apply)
        return
    if sid == lib.ST["paid"]:
        if not tasks:
            add_task(amo, lead, "Автомаршрут не сработал: производство или сборка", n + timedelta(hours=1), None, apply)
        return
    if sid == lib.ST["prod"]:
        if not has_text(tasks, "готовность"):
            add_task(amo, lead, "Проверить готовность / актуализировать", n, None, apply)
            add_task(amo, lead, "Копия Оксане: проверить готовность", n, lib.USER_OKSANA, apply)
        return
    if sid == lib.ST["pack"]:
        if not tasks:
            add_task(amo, lead, "Собрать заказ сегодня", n, None, apply)
        return
    if sid == lib.ST["sent"]:
        track = amo.cf(lead, lib.FIELD_TRACK) or amo.cf(lead, lib.FIELD_CDEK) or amo.cf(lead, lib.FIELD_TRACK_MS)
        if not track and not has_text(tasks, "трек"):
            add_task(amo, lead, "Проставить трек-номер", n, None, apply)
        return
    if sid == lib.ST["won"]:
        if not has_text(tasks, "обратн"):
            add_task(amo, lead, "Обратная связь: всё ли подошло", n + timedelta(days=2), None, apply)


def process_mkt(amo: lib.Amo, lead: dict, apply: bool) -> None:
    if lead.get("status_id") != lib.ST["mkt_talk"]:
        return
    tasks = open_tasks(amo, lead["id"])
    if not tasks:
        add_task(amo, lead, "Маркетинг: взять в работу", now(), lib.USER_POLINA, apply)
    hours = created_hours(lead)
    if hours >= 0.5 and not has_text(tasks, "повтор 30"):
        add_task(amo, lead, "Повтор 30 мин маркетинг", now(), lib.USER_POLINA, apply)
    if hours >= 2 and not has_text(tasks, "оксане"):
        add_task(amo, lead, "Эскалация Оксане: маркетинг 2 часа", now(), lib.USER_OKSANA, apply)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    amo = lib.Amo()
    sales = amo.iter_leads(f"?filter[pipeline_id]={lib.PIPELINE_SALES_NEW}")
    mkt = amo.iter_leads(f"?filter[pipeline_id]={lib.PIPELINE_MKT_NEW}")
    print(f"Продажи 2MY: {len(sales)}  Маркетинг 2MY: {len(mkt)}  apply={args.apply}")
    for lead in sales:
        if lead.get("status_id") in (lib.ST["lost"],):
            continue
        process_sales(amo, lead, args.apply)
    for lead in mkt:
        process_mkt(amo, lead, args.apply)


if __name__ == "__main__":
    main()
