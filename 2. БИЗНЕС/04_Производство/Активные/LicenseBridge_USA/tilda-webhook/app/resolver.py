"""Read-only резолвер для телефонии: телефон → сделка → ответственный.
Владельца не назначает (автоназначение Елены — в плане телефонии)."""
from __future__ import annotations

from typing import Any

from app.config import CLOSED_STATUS_IDS, settings
from app.dedup_deals import load_leads
from app.identity import find_contacts_by_phone, normalize_phone
from app.kommo.client import KommoClient


def _assembly_first(opens: list[dict[str, Any]]) -> dict[str, Any]:
    """Из открытых сделок берём сделку производства, если она есть.

    У клиента, дошедшего до «Сборки», сделка продажи закрыта, но рядом может
    висеть открытая заявка из рекламы. Ведёт такого клиента производство, поэтому
    старшинство по дате уступает воронке."""
    for lead in opens:
        if lead.get("pipeline_id") == settings.assembly_pipeline_id:
            return lead
    return opens[0]


def resolve_responsible(client: KommoClient, raw_phone: str) -> dict[str, Any]:
    phone = normalize_phone(raw_phone)
    if not phone:
        return {"phone": raw_phone, "found": False, "reason": "bad_phone"}
    contacts = find_contacts_by_phone(client, phone)
    if not contacts:
        return {"phone": phone, "found": False, "reason": "no_contact"}
    # старший контакт
    contact = sorted(contacts, key=lambda c: c.get("created_at") or 0)[0]
    leads = load_leads(client, int(contact["id"]))
    opens = sorted(
        [l for l in leads
         if l.get("closed_at") in (None, 0) and l.get("status_id") not in CLOSED_STATUS_IDS],
        key=lambda l: l.get("created_at") or 0,
    )
    if opens:
        # воронка нужна маршрутизации: клиента в «Сборке» ведёт клиентский отдел,
        # звонок такого клиента в продажи не отдаём (просьба 03.09.2026)
        lead = _assembly_first(opens)
        return {"phone": phone, "found": True, "contact_id": contact["id"],
                "lead_id": lead["id"], "responsible_user_id": lead.get("responsible_user_id"),
                "pipeline_id": lead.get("pipeline_id"), "status": "open"}
    closed = sorted([l for l in leads if l.get("closed_at")],
                    key=lambda l: l.get("closed_at") or 0, reverse=True)
    if closed:
        lead = closed[0]
        return {"phone": phone, "found": True, "contact_id": contact["id"],
                "lead_id": lead["id"], "responsible_user_id": lead.get("responsible_user_id"),
                "pipeline_id": lead.get("pipeline_id"), "status": "closed"}
    return {"phone": phone, "found": True, "contact_id": contact["id"],
            "responsible_user_id": settings.default_sales_owner_id, "status": "no_lead"}
