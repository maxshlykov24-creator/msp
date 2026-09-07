#!/usr/bin/env python3
"""Переписка, осевшая на дублях: положить её адрес в рабочую карточку.

Разбор 19.08.2026: за август в аккаунте 291 входящее и 968 исходящих сообщений,
канал Wazzup живой, привязка к карточкам верная. Но 41 сделка с перепиской —
дубли и «Провал», и на них осталось 98 сообщений клиентов. Живая парная карточка
при этом пустая: сообщения физически переносить нельзя, API Kommo не даёт создать
`amomessage`. Пример: на дубле 29892373 — 21 сообщение клиента, в рабочей 29875035
входящих ноль.

Значит единственное, что можно сделать: в рабочей карточке оставить цифры и
прямой адрес переписки. Дальше это делает сам дедуп (`dedup_deals._dedup_pair`),
а скрипт закрывает то, что уже накопилось.

Пару «дубль → живая сделка» ищем в таком порядке:
1. примечание «Дубль сделки #N» на дубле — самый надёжный след нашей же работы;
2. если следа нет (28 дублей из 41 такие) — открытая сделка того же контакта.
Не нашли ни того ни другого — сделка идёт в отчёт как ручной разбор, наугад
писать не будем.

Идемпотентность: перед записью ищем в живой карточке адрес этого дубля.

    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - list 2026-08-01" < scripts/chat_digest_backfill.py
    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - apply 2026-08-01" < scripts/chat_digest_backfill.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.chat_events import ChatStats, fetch, lead_url
from app.config import CLOSED_STATUS_IDS, settings
from app.kommo.client import KommoClient

DUP_NOTE = re.compile(r"[Дд]убл[ья] сделки #(\d+)")


def _ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _is_parked(lead: dict[str, Any]) -> bool:
    """Сделка снята с работы: «Провал», архивная воронка или тег дубля."""
    status = int(lead.get("status_id") or 0)
    tags = {t.get("name") for t in ((lead.get("_embedded") or {}).get("tags") or [])}
    if settings.tag_dup_deal in tags:
        return True
    if status in CLOSED_STATUS_IDS:
        return True
    return bool(settings.archive_pipeline_id
                and int(lead.get("pipeline_id") or 0) == settings.archive_pipeline_id)


def _survivor(client: KommoClient, dup: dict[str, Any]) -> tuple[int | None, str]:
    dup_id = int(dup["id"])
    for note in client.get_notes("leads", dup_id):
        text = (note.get("params") or {}).get("text") or ""
        m = DUP_NOTE.search(str(text))
        if m and int(m.group(1)) != dup_id:
            return int(m.group(1)), "примечание"
    # следа нет — берём открытую сделку того же контакта
    contacts = (dup.get("_embedded") or {}).get("contacts") or []
    open_leads: list[dict[str, Any]] = []
    for c in contacts:
        data = client.get_contact(int(c["id"]), with_="leads") or {}
        for lead_ref in ((data.get("_embedded") or {}).get("leads") or []):
            lid = int(lead_ref["id"])
            if lid == dup_id:
                continue
            lead = client.get_lead(lid, with_="contacts") or {}
            if lead and not _is_parked(lead):
                open_leads.append(lead)
    if len(open_leads) == 1:
        return int(open_leads[0]["id"]), "открытая сделка контакта"
    if len(open_leads) > 1:
        newest = max(open_leads, key=lambda l: l.get("created_at") or 0)
        return int(newest["id"]), f"новейшая из {len(open_leads)} открытых"
    return None, "живой карточки нет"


def _already_written(client: KommoClient, survivor_id: int, dup_id: int) -> bool:
    marker = lead_url(dup_id)
    for note in client.get_notes("leads", survivor_id):
        if marker in str((note.get("params") or {}).get("text") or ""):
            return True
    return False


def _body(dup_id: int, st: ChatStats) -> str:
    return (f"Переписка по этому клиенту осталась в карточке #{dup_id} "
            f"(дубль, снят с работы): {st.describe()}\n"
            f"Открыть переписку: {lead_url(dup_id)}\n"
            f"Перенести сообщения в эту карточку API Kommo не позволяет, "
            f"поэтому здесь только адрес и цифры.")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    since_day = sys.argv[2] if len(sys.argv) > 2 else "2026-08-01"
    # Одна исходящая SMS бота на мёртвом дубле — не «история переписки», и
    # примечание о ней только зашумит карточку. Пишем там, где говорил клиент.
    min_incoming = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    apply = mode == "apply"

    client = KommoClient()
    stats = fetch(client, _ts(since_day))
    print(f"сделок с перепиской с {since_day}: {len(stats)}")

    written = skipped = 0
    orphans: list[ChatStats] = []      # переписка есть, живой карточки нет
    for lead_id, st in sorted(stats.items(), key=lambda kv: -kv[1].incoming):
        if st.incoming < min_incoming:
            continue
        lead = client.get_lead(lead_id, with_="contacts") or {}
        if not lead or not _is_parked(lead):
            continue
        survivor_id, how = _survivor(client, lead)
        if not survivor_id:
            orphans.append(st)
            continue
        if _already_written(client, survivor_id, lead_id):
            skipped += 1
            continue
        print(f"{'ПИШУ' if apply else 'ПЛАН'} #{survivor_id} ← дубль #{lead_id} "
              f"({how}): {st.describe()}")
        if apply:
            client.add_note("leads", survivor_id, _body(lead_id, st))
            written += 1

    if orphans:
        print("\nПереписка есть, а живой карточки у клиента нет — это уже не "
              "техника, а решение менеджера, писать некуда:")
        for st in orphans:
            print(f"  #{st.lead_id}: {st.describe()}\n    {lead_url(st.lead_id)}")

    print(f"\nитог: записано {written}, уже было {skipped}, "
          f"без живой карточки {len(orphans)}")
    if not apply:
        print(f"это был предпросмотр, для записи: python - apply {since_day} {min_incoming}")
    client.close()


if __name__ == "__main__":
    main()
