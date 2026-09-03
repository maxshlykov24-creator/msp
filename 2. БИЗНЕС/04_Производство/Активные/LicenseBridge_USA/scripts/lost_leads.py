#!/usr/bin/env python3
"""Сверка: каким заявкам никто не ответил.

Запускать в контейнере хаба, где уже есть токен Kommo:

    ssh licensebridge-hub 'docker exec lb-hub-api python /opt/lb/lost_leads.py 2026-08-26 2026-09-03'

Для каждой заявки Pipeline за период проверяем три следа работы: переписка,
исходящий звонок и задача. Нет ни одного — заявку не тронули. Вывод markdown,
чтобы отдать Павлу как есть.

Pleep в сверку не входит: доступа в его кабинет у нас нет, есть только наш
входящий ключ `/pleep/sync`. Что не долетело со стороны Pleep, по Kommo не видно.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone

from app.chat_events import fetch as fetch_chats
from app.config import CLOSED_STATUS_IDS, settings
from app.identity import contact_phones
from app.kommo.client import KommoClient

# карточки из чата с Павлом: их смотрим отдельно, даже если следы работы есть
WATCH = [30381719, 30381059, 30436589, 30505461, 29867259, 30491667, 29966931]


def _ts(day: str, end: bool = False) -> int:
    dt = datetime.strptime(day, "%Y-%m-%d").replace(
        hour=23 if end else 0, minute=59 if end else 0, second=59 if end else 0,
        tzinfo=timezone.utc)
    return int(dt.timestamp())


def _day(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%d.%m %H:%M")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    since, until = _ts(argv[1]), _ts(argv[2], end=True)
    client = KommoClient()

    users = {int(u["id"]): u.get("name") or f"id {u['id']}" for u in client.users()}

    leads = [
        lead for lead in client.paginate(
            "/leads", "leads", {"filter[pipeline_id]": settings.pipeline_id,
                                "with": "contacts"})
        if since <= int(lead.get("created_at") or 0) <= until
    ]

    # переписка: /events с фильтром по типу — единственный способ увидеть чаты
    chats = fetch_chats(client, since - 3 * 86400)

    # исходящие звонки и задачи по каждой сделке
    called: dict[int, int] = {}
    for note in client.paginate("/leads/notes", "notes",
                                {"filter[note_type][]": "call_out"}):
        lead_id = int(note.get("entity_id") or 0)
        ts = int(note.get("created_at") or 0)
        if lead_id and ts >= called.get(lead_id, 0):
            called[lead_id] = ts
    tasks: dict[int, int] = {}
    for task in client.paginate("/tasks", "tasks", {"filter[entity_type]": "leads"}):
        lead_id = int(task.get("entity_id") or 0)
        if lead_id:
            tasks[lead_id] = tasks.get(lead_id, 0) + 1

    rows = []
    for lead in leads:
        lead_id = int(lead["id"])
        phone = ""
        for c in ((lead.get("_embedded") or {}).get("contacts")) or []:
            full = client.get_contact(int(c["id"]), with_=None)
            phones = contact_phones(full or {})
            if phones:
                phone = phones[0]
                break
        stats = chats.get(lead_id)
        outgoing_msg = bool(stats and stats.outgoing)
        rows.append({
            "id": lead_id,
            "phone": phone or "нет номера",
            "created": int(lead.get("created_at") or 0),
            "owner": users.get(int(lead.get("responsible_user_id") or 0), "не назначен"),
            "closed": lead.get("status_id") in CLOSED_STATUS_IDS,
            "msg": outgoing_msg,
            "call": called.get(lead_id, 0),
            "tasks": tasks.get(lead_id, 0),
        })

    untouched = [r for r in rows
                 if not r["msg"] and not r["call"] and not r["tasks"] and not r["closed"]]
    no_message = [r for r in rows if not r["msg"] and r not in untouched]

    print(f"# Заявки без ответа, {argv[1]} — {argv[2]}\n")
    print(f"Заявок в Pipeline за период: **{len(rows)}**. "
          f"Без единого следа работы (нет сообщения, звонка и задачи): "
          f"**{len(untouched)}**. Без исходящего сообщения, но с звонком или "
          f"задачей: **{len(no_message)}**.\n")
    print("Pleep в сверке нет: доступа в его кабинет у нас нет, по Kommo не видно, "
          "что до него не долетело.\n")

    print("## Никто не тронул\n")
    if untouched:
        print("| Сделка | Телефон | Заявка | Ответственный | Что не сработало |")
        print("|---|---|---|---|---|")
        for r in sorted(untouched, key=lambda r: r["created"]):
            print(f"| [{r['id']}](https://licensebridgeusa.kommo.com/leads/detail/{r['id']}) "
                  f"| {r['phone']} | {_day(r['created'])} | {r['owner']} "
                  f"| первое сообщение не ушло, звонка и задачи нет |")
    else:
        print("Таких заявок нет.")

    print("\n## Без первого сообщения, но в работе\n")
    if no_message:
        print("| Сделка | Телефон | Заявка | Ответственный | Звонок | Задач |")
        print("|---|---|---|---|---|---|")
        for r in sorted(no_message, key=lambda r: r["created"]):
            print(f"| [{r['id']}](https://licensebridgeusa.kommo.com/leads/detail/{r['id']}) "
                  f"| {r['phone']} | {_day(r['created'])} | {r['owner']} "
                  f"| {_day(r['call'])} | {r['tasks']} |")
    else:
        print("Таких заявок нет.")

    print("\n## Карточки из переписки\n")
    print("| Сделка | Телефон | Воронка | Этап | Ответственный | Сообщение | Звонок | Задач |")
    print("|---|---|---|---|---|---|---|---|")
    pipelines = {int(p["id"]): p.get("name") or "" for p in client.pipelines()}
    for lead_id in WATCH:
        lead = client.get_lead(lead_id, with_="contacts")
        if not lead:
            print(f"| {lead_id} | — | сделки нет в Kommo | | | | | |")
            continue
        phone = ""
        for c in ((lead.get("_embedded") or {}).get("contacts")) or []:
            full = client.get_contact(int(c["id"]), with_=None)
            phones = contact_phones(full or {})
            if phones:
                phone = phones[0]
                break
        stats = chats.get(lead_id)
        print(f"| [{lead_id}](https://licensebridgeusa.kommo.com/leads/detail/{lead_id}) "
              f"| {phone or '—'} | {pipelines.get(int(lead.get('pipeline_id') or 0), '?')} "
              f"| {lead.get('status_id')} "
              f"| {users.get(int(lead.get('responsible_user_id') or 0), 'не назначен')} "
              f"| {'да' if stats and stats.outgoing else 'нет'} "
              f"| {_day(called.get(lead_id, 0))} | {tasks.get(lead_id, 0)} |")

    print("\n## Ответственные по заявкам периода\n")
    for name, count in Counter(r["owner"] for r in rows).most_common():
        print(f"- {name}: {count}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
