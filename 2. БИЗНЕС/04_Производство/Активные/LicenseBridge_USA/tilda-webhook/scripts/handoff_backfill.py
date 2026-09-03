#!/usr/bin/env python3
"""Разбор накопившихся won: сделки Pipeline, выигранные пока `ENABLE_HANDOFF`
был выключен, и потому не породившие сделку в «Сборке».

Флаг включили только 13.08.2026, а код handoff живёт с 24.07 — за это время
каждый won молча получал `handoff.shadow` и уходил в никуда. Скрипт находит
такие сделки и прогоняет их через штатный `run_handoff` (та же идемпотентность
и те же guard'ы, что у вебхука).

Пропускаем сделку, если у её контакта уже есть ЛЮБАЯ сделка в «Сборке»: за
время без автоматики менеджеры заводили производство руками, и второй перенос
дал бы дубль, который система дублем не считает (тег `сборка_из_продажи`).

Скрипт не входит в образ, подаётся на stdin:

    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - list 2026-07-24" < scripts/handoff_backfill.py
    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - apply 2026-07-24" < scripts/handoff_backfill.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any

from app.actions import Ctx
from app.config import settings
from app.db import session_scope
from app.handoff import run_handoff
from app.kommo.client import KommoClient

RATE_LIMIT_SLEEP_SEC = 300


def _ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _won_leads(client: KommoClient, since: int) -> list[dict[str, Any]]:
    params = {
        "limit": 250,
        "with": "contacts",
        "filter[statuses][0][pipeline_id]": settings.pipeline_id,
        "filter[statuses][0][status_id]": settings.status_won,
        "filter[closed_at][from]": since,
    }
    return list(client.paginate("/leads", "leads", params=params))


def _has_assembly(client: KommoClient, contact_id: int) -> int | None:
    contact = client.get_contact(contact_id, with_="leads")
    for ref in ((contact or {}).get("_embedded") or {}).get("leads") or []:
        lead = client.get_lead(int(ref["id"]), with_="contacts")
        if lead and int(lead.get("pipeline_id") or 0) == settings.assembly_pipeline_id:
            return int(lead["id"])
    return None


def _candidates(client: KommoClient, since: int) -> tuple[list[dict], list[dict]]:
    todo: list[dict] = []
    skip: list[dict] = []
    for lead in _won_leads(client, since):
        contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
        row = {"id": int(lead["id"]), "name": lead.get("name"),
               "closed_at": lead.get("closed_at"), "price": lead.get("price")}
        if not contacts:
            row["reason"] = "нет контакта"
            skip.append(row)
            continue
        row["contact"] = int(contacts[0]["id"])
        assembly = _has_assembly(client, row["contact"])
        if assembly:
            row["reason"] = f"у контакта уже есть сделка в Сборке #{assembly}"
            skip.append(row)
            continue
        todo.append(row)
    return todo, skip


def _print(todo: list[dict], skip: list[dict]) -> None:
    print(f"\nК переносу: {len(todo)}")
    for r in todo:
        closed = datetime.fromtimestamp(r["closed_at"] or 0, timezone.utc).date()
        print(f"  {r['id']}  {closed}  {r.get('price')}  {r['name']}")
    print(f"\nПропущено: {len(skip)}")
    for r in skip:
        print(f"  {r['id']}  {r['name']} — {r['reason']}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    since = _ts(sys.argv[2] if len(sys.argv) > 2 else "2026-07-24")
    client = KommoClient()
    todo, skip = _candidates(client, since)
    _print(todo, skip)
    if mode != "apply":
        print("\nсухой прогон, ничего не создано")
        return

    print("\n— применяю —")
    for r in todo:
        while True:
            with session_scope() as s:
                ctx = Ctx(client, s, inbox_id=None, phone=None, shadow=False)
                res = run_handoff(ctx, r["id"])
            if res["action"] != "handoff.rate_limited":
                break
            print(f"  лимит {settings.handoff_max_per_hour}/час, пауза {RATE_LIMIT_SLEEP_SEC}с")
            time.sleep(RATE_LIMIT_SLEEP_SEC)
        print(f"  {r['id']} → {res['action']} {res.get('assembly_lead') or ''}")


if __name__ == "__main__":
    main()
