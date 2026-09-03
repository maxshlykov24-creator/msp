"""Единый приём лидов из каналов (Tilda, Pleep, Facebook/Make и др.).

Предотвращение дублей ДО записи: по телефону ищем контакт; при открытой сделке
новую не создаём (пишем источник в примечание). Новые не-телефонные лиды → Илона.
"""
from __future__ import annotations

import logging
from typing import Any

from app.actions import Ctx, add_note, log_decision
from app.assignment import resolve_owner_for_new, sync_contact_owner
from app.config import CLOSED_STATUS_IDS, settings
from app.dedup_deals import load_leads, resolve as resolve_deals
from app.identity import find_contacts_by_phone, normalize_phone
from app.dedup_contacts import resolve as resolve_contacts

log = logging.getLogger("intake")


def _can_mutate(ctx: Ctx) -> bool:
    """Создание сущностей разрешено вне shadow, либо в shadow при intake_create_in_shadow."""
    return (not ctx.shadow) or settings.intake_create_in_shadow


def _find_open_pipeline_lead(leads: list[dict[str, Any]]) -> dict[str, Any] | None:
    opens = [l for l in leads
             if l.get("closed_at") in (None, 0)
             and l.get("status_id") not in CLOSED_STATUS_IDS
             and l.get("pipeline_id") in (settings.pipeline_id, settings.assembly_pipeline_id)]
    opens.sort(key=lambda l: l.get("created_at") or 0)
    return opens[0] if opens else None


def _create_contact(ctx: Ctx, name: str, phone: str, email: str) -> int | None:
    cf: list[dict[str, Any]] = []
    if phone:
        cf.append({"field_id": settings.field_phone,
                   "values": [{"value": phone, "enum_id": settings.field_phone_enum_work}]})
    payload = [{"name": name or phone or email or "Lead", "custom_fields_values": cf}]
    if not _can_mutate(ctx):
        log_decision(ctx, "intake.create_contact.shadow", name=name, phone=phone)
        return None
    data = ctx.client.create_contact(payload)
    cid = data["_embedded"]["contacts"][0]["id"] if data else None
    log_decision(ctx, "intake.create_contact", contact=cid, phone=phone)
    return cid


def _create_lead(ctx: Ctx, name: str, contact_id: int, channel: str,
                 owner: int, utm_source: str, utm_campaign: str) -> int | None:
    cf = [{"field_id": settings.field_channel, "values": [{"value": channel}]}]
    if utm_source:
        cf.append({"field_id": settings.field_utm_source, "values": [{"value": utm_source}]})
    if utm_campaign:
        cf.append({"field_id": settings.field_utm_campaign, "values": [{"value": utm_campaign}]})
    payload = [{
        "name": name or "Lead",
        "pipeline_id": settings.pipeline_id,
        "status_id": settings.status_new,
        "responsible_user_id": owner,
        "custom_fields_values": cf,
        "_embedded": {"contacts": [{"id": contact_id}]},
    }]
    if not _can_mutate(ctx):
        log_decision(ctx, "intake.create_lead.shadow", contact=contact_id, channel=channel, owner=owner)
        return None
    data = ctx.client.create_lead(payload)
    lid = data["_embedded"]["leads"][0]["id"] if data else None
    log_decision(ctx, "intake.create_lead", lead=lid, contact=contact_id, channel=channel, owner=owner)
    return lid


def process_intake(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    phone = normalize_phone(payload.get("phone"))
    email = (payload.get("email") or "").strip()
    channel = (payload.get("channel") or payload.get("source") or "").strip() or "unknown"
    notes = (payload.get("notes") or "").strip()
    utm_source = (payload.get("utm_source") or "").strip()
    utm_campaign = (payload.get("utm_campaign") or "").strip()

    # 1. контакт по телефону (dedup контактов, если несколько)
    contacts = find_contacts_by_phone(ctx.client, phone) if phone else []
    group: list[int] = []
    if contacts:
        cres = resolve_contacts(ctx, contacts, has_phone=bool(phone))
        contact_id = cres.get("contact_id")
        group = cres.get("group") or []
    elif phone or name or email:
        contact_id = _create_contact(ctx, name, phone, email)
    else:
        log_decision(ctx, "intake.empty", payload_keys=list(payload.keys()))
        return {"action": "empty"}

    if not contact_id:
        return {"action": "shadow_no_contact"}

    # 2. открытая сделка есть → не плодим новую (повтор), пишем источник.
    # Смотрим по всей группе карточек номера: иначе на несклеенном дубле открытая
    # сделка не видна и заявка создаёт ещё одну.
    leads = load_leads(ctx.client, group or int(contact_id))
    open_lead = _find_open_pipeline_lead(leads)
    if open_lead:
        note = f"Повторное обращение из {channel}."
        if notes:
            note += f"\n{notes}"
        add_note(ctx, "deal", int(open_lead["id"]), note)
        log_decision(ctx, "intake.repeat_attached", lead=open_lead["id"], channel=channel)
        # на всякий случай подчистить дубли, если их несколько
        resolve_deals(ctx, int(contact_id), None, trigger="intake", group_contact_ids=group)
        return {"action": "repeat", "lead_id": open_lead["id"], "contact_id": contact_id}

    # 3. новой сделки нет → создать, назначить ответственного (Илона / наследование)
    owner = resolve_owner_for_new(ctx, leads)
    lead_id = _create_lead(ctx, name, int(contact_id), channel, owner, utm_source, utm_campaign)
    if lead_id and notes:
        add_note(ctx, "deal", lead_id, notes)
    if lead_id and _can_mutate(ctx):
        created = ctx.client.get_lead(int(lead_id)) or {
            "id": lead_id, "_embedded": {"contacts": [{"id": int(contact_id)}]},
        }
        sync_contact_owner(ctx, created, owner)
    log_decision(ctx, "intake.created", lead=lead_id, contact=contact_id, owner=owner, channel=channel)
    return {"action": "created", "lead_id": lead_id, "contact_id": contact_id, "owner": owner}
