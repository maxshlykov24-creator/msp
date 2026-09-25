"""Питомник: «Взято в работу» без общения дольше 30 дней уходит в провал.

Срок считается от последнего обновления беседы amoCRM. Если беседы нет,
от даты создания сделки. Бронь и ожидание помёта этот проход не закрывает:
там тишина не значит отказ.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import amocrm_client

log = logging.getLogger("keris.kennel.silence")

PIPE = 11036674
IN_WORK = 86717270
LOST = 143
REASON = 1820879
REASON_SILENCE = 3087167
SILENCE_DAYS = 30
MSK = timezone(timedelta(hours=3))


def _pages(path: str, key: str, params: dict) -> list[dict]:
    rows = []
    page = 1
    while page <= 40:
        query = dict(params)
        query["page"] = page
        query["limit"] = 250
        data = amocrm_client._request("GET", path, params=query)
        chunk = ((data or {}).get("_embedded") or {}).get(key) or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 250:
            break
        page += 1
    return rows


def run_once() -> int:
    if not amocrm_client.settings.amocrm_ready or amocrm_client.blocked():
        return 0
    cut = int((datetime.now(MSK) - timedelta(days=SILENCE_DAYS)).timestamp())
    last: dict[int, int] = {}
    for talk in _pages("/api/v4/talks", "talks", {}):
        if talk.get("entity_type") != "lead" or not talk.get("entity_id"):
            continue
        eid = int(talk["entity_id"])
        last[eid] = max(last.get(eid, 0), int(talk.get("updated_at") or 0))
    silent = []
    for lead in _pages("/api/v4/leads", "leads", {
        "filter[pipeline_id]": PIPE,
        "filter[statuses][0][pipeline_id]": PIPE,
        "filter[statuses][0][status_id]": IN_WORK,
    }):
        lid = int(lead["id"])
        stamp = last.get(lid) or int(lead.get("created_at") or 0)
        if stamp <= cut:
            silent.append(lid)
    closed = 0
    for i in range(0, len(silent), 50):
        batch = [{
            "id": lid,
            "pipeline_id": PIPE,
            "status_id": LOST,
            "custom_fields_values": [{
                "field_id": REASON,
                "values": [{"enum_id": REASON_SILENCE}],
            }],
        } for lid in silent[i:i + 50]]
        amocrm_client._request("PATCH", "/api/v4/leads", json=batch)
        closed += len(batch)
    if closed:
        log.info("питомник: в провал из-за тишины %s", closed)
    return closed
