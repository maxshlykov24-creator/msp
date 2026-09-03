"""Read-only резолвер для телефонии: телефон → сделка → ответственный.
Владельца не назначает (автоназначение Елены — в плане телефонии)."""
from __future__ import annotations

from typing import Any

from app.config import CLOSED_STATUS_IDS, settings
from app.dedup_deals import load_leads
from app.identity import find_contacts_by_phone, normalize_phone
from app.kommo.client import KommoClient


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
        lead = opens[0]
        return {"phone": phone, "found": True, "contact_id": contact["id"],
                "lead_id": lead["id"], "responsible_user_id": lead.get("responsible_user_id"),
                "status": "open"}
    closed = sorted([l for l in leads if l.get("closed_at")],
                    key=lambda l: l.get("closed_at") or 0, reverse=True)
    if closed:
        lead = closed[0]
        return {"phone": phone, "found": True, "contact_id": contact["id"],
                "lead_id": lead["id"], "responsible_user_id": lead.get("responsible_user_id"),
                "status": "closed"}
    return {"phone": phone, "found": True, "contact_id": contact["id"],
            "responsible_user_id": settings.default_sales_owner_id, "status": "no_lead"}
