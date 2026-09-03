"""Handoff Pipeline → Сборка: won в продажах создаёт новую сделку производства.

Сохранение истории — НЕ перенос одной карточки между воронками (это ломает
аналитику won/конверсии Pipeline, см. ТЗ §7.1), а создание НОВОЙ сделки в
«Сборке» на том же контакте с backdate `created_at`. Лента карточки Kommo
показывает события начиная примерно за 24ч до `created_at` самой сделки
(документированное поведение amoCRM/Kommo) — совпадающая дата подтягивает
прежние звонки и чаты контакта в новую карточку.

`run_handoff` — единая точка входа для webhook-пути (`worker.handle_kommo_
status_lead`) и ручного `/internal/handoff`: обе стороны обязаны видеть
одинаковые guard'ы, иначе легко разойтись и создать дубли или петлю (в
«Сборке» этап «Экзамен назначен» тоже имеет status_id=142 — без проверки
pipeline_id получили бы бесконечное размножение сделок).

Guard по pipeline_id/status_id — быстрый и БЕЗ log_decision: worker.py зовёт
эту функцию на каждый status_lead (любой этап, любая воронка), а не только на
won в Pipeline, и не должен раздувать журнал решений транзитными переходами.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.actions import Ctx, add_note, add_tags, current_tags, log_decision
from app.config import settings
from app.dedup_deals import load_leads
from app.models import Handoff

log = logging.getLogger("handoff")

# Типы кастомных полей сделки, которые можно скопировать значением 1:1.
# Не копируем служебные/агрегируемые/файловые (tracking_data, file, category,
# chained_list, price — price копируется отдельно нативным полем сделки).
_COPYABLE_FIELD_TYPES = {
    "text", "numeric", "textarea", "select", "multiselect", "checkbox",
    "url", "date", "date_time", "radiobutton", "streetaddress",
    "smart_address", "legal_entity", "birthday",
}

# Примечания, которые копируются как есть (текст + исходная дата).
_COPYABLE_NOTE_TYPES = {"common", "service_message"}
# Примечания, которые не копируются 1:1 (звонки/чаты нельзя создать «как
# было» через API), а сводятся в один дайджест-note для контекста менеджера.
_DIGEST_NOTE_TYPES = {"call_in", "call_out", "amomessage", "chat_message"}


def _lead_contacts(lead: dict[str, Any]) -> list[dict[str, Any]]:
    return ((lead.get("_embedded") or {}).get("contacts")) or []


def _lead_companies(lead: dict[str, Any]) -> list[dict[str, Any]]:
    return ((lead.get("_embedded") or {}).get("companies")) or []


def _already_in_registry(ctx: Ctx, source_lead_id: int) -> Handoff | None:
    return ctx.s.scalar(select(Handoff).where(Handoff.source_lead_id == source_lead_id))


def _already_in_kommo(ctx: Ctx, contact_id: int, source_lead_id: int) -> int | None:
    """Страховка поверх реестра `handoffs`: ищет уже существующую сборочную
    сделку по тегу + ссылке на исходную сделку в примечании — на случай
    потери/отката локальной БД, когда реестр не помнит прошлый перенос."""
    marker = f"Продажа: сделка #{source_lead_id}."
    for lead in load_leads(ctx.client, contact_id):
        if lead.get("pipeline_id") != settings.assembly_pipeline_id:
            continue
        if settings.handoff_tag not in current_tags(lead):
            continue
        try:
            notes = ctx.client.get_notes("leads", int(lead["id"]))
        except Exception:
            continue
        if any(marker in ((n.get("params") or {}).get("text") or "") for n in notes):
            return int(lead["id"])
    return None


def _rate_limited(ctx: Ctx) -> bool:
    """Не больше `handoff_max_per_hour` переносов за скользящий час — защита
    от петли и от массового ошибочного перетаскивания менеджерами."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    count = ctx.s.scalar(
        select(func.count()).select_from(Handoff).where(Handoff.created_at >= since)
    ) or 0
    return count >= settings.handoff_max_per_hour


def _copyable_field_ids(ctx: Ctx) -> set[int]:
    try:
        fields = ctx.client.custom_fields("leads")
    except Exception as exc:
        log.warning("handoff: custom_fields(leads) failed: %s", exc)
        return set()
    return {int(f["id"]) for f in fields
            if f.get("id") and f.get("type") in _COPYABLE_FIELD_TYPES}


def _copy_fields(ctx: Ctx, source: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = _copyable_field_ids(ctx)
    out: list[dict[str, Any]] = []
    for cf in source.get("custom_fields_values") or []:
        fid = cf.get("field_id")
        if fid in allowed and cf.get("values"):
            out.append({"field_id": fid, "values": cf["values"]})
    return out


def _create_assembly_lead(ctx: Ctx, source: dict[str, Any], contact_id: int) -> int | None:
    payload: dict[str, Any] = {
        "name": source.get("name") or "Сборка",
        "pipeline_id": settings.assembly_pipeline_id,
        "status_id": settings.assembly_status_start,
        "responsible_user_id": settings.handoff_owner_id,
        "custom_fields_values": _copy_fields(ctx, source),
        "_embedded": {"contacts": [{"id": contact_id}]},
    }
    if source.get("price"):
        payload["price"] = source["price"]
    if settings.handoff_backdate and source.get("created_at"):
        payload["created_at"] = source["created_at"]
    companies = _lead_companies(source)
    if companies:
        payload["_embedded"]["companies"] = [{"id": int(companies[0]["id"])}]

    data = ctx.client.create_lead([payload])
    return data["_embedded"]["leads"][0]["id"] if data else None


def _copy_history(ctx: Ctx, source_id: int, assembly_id: int) -> None:
    """Копирует читаемый текст примечаний; звонки/чат сводит в один дайджест
    (создать «как было» через API нельзя — это не notes, а talks/события)."""
    try:
        notes = ctx.client.get_notes("leads", source_id)
    except Exception as exc:
        log.warning("handoff: notes fetch failed lead=%s: %s", source_id, exc)
        return

    digest_lines: list[str] = []
    for n in notes:
        nt = n.get("note_type", "")
        params = n.get("params") or {}
        text = params.get("text") or params.get("comment")
        if nt in _COPYABLE_NOTE_TYPES and text:
            note_payload: dict[str, Any] = {
                "entity_id": assembly_id, "note_type": nt, "params": {"text": text},
            }
            if n.get("created_at"):
                note_payload["created_at"] = n["created_at"]
            ctx.client.add_note_raw("leads", note_payload)
        elif nt in _DIGEST_NOTE_TYPES:
            if nt in ("call_in", "call_out"):
                dur = params.get("duration")
                digest_lines.append(f"[звонок {nt} {dur or ''}] {text or ''}".strip())
            else:
                digest_lines.append(f"[{nt}] {text or ''}".strip())

    if digest_lines:
        body = (f"Дайджест звонков/чата из продажи #{source_id} "
                f"(полная лента — в исходной сделке):\n" +
                "\n".join(f"— {x}" for x in digest_lines[-50:]))
        add_note(ctx, "deal", assembly_id, body)


def run_handoff(ctx: Ctx, lead_id: int, lead: dict[str, Any] | None = None) -> dict[str, Any]:
    """Точка входа. Безопасно вызывать на любое событие status_lead — сделки,
    не подходящие под условия (не Pipeline, не won, уже перенесённая), молча
    возвращают action=handoff.skip_* без побочных эффектов и без шума в
    журнале решений.

    `lead` — опциональный уже загруженный лид (с `with=contacts`), чтобы
    вызывающий код (worker, уже сходивший в Kommo за лидом) не платил вторым
    запросом; ручной `/internal/handoff` передаёт только `lead_id`."""
    if lead is None:
        lead = ctx.client.get_lead(lead_id, with_="contacts")
    if not lead:
        return {"action": "handoff.lead_gone", "lead": lead_id}

    # Быстрый безшумный guard: воронка Pipeline; и won; в Сборке этап
    # «Экзамен назначен» тоже status_id=142 — без pipeline_id завернёт петлю.
    if int(lead.get("pipeline_id") or 0) != settings.pipeline_id:
        return {"action": "handoff.skip_pipeline", "lead": lead_id, "pipeline": lead.get("pipeline_id")}
    if lead.get("status_id") != settings.status_won:
        return {"action": "handoff.skip_status", "lead": lead_id, "status": lead.get("status_id")}

    if {settings.tag_dup_deal, settings.tag_dup_to_delete} & set(current_tags(lead)):
        log_decision(ctx, "handoff.skip_dup_tagged", lead=lead_id)
        return {"action": "handoff.skip_dup_tagged", "lead": lead_id}

    existing = _already_in_registry(ctx, lead_id)
    if existing:
        log_decision(ctx, "handoff.already_registry", lead=lead_id, assembly=existing.assembly_lead_id)
        return {"action": "handoff.already", "lead": lead_id, "assembly_lead": existing.assembly_lead_id}

    contacts = _lead_contacts(lead)
    if not contacts:
        log_decision(ctx, "handoff.no_contact", lead=lead_id)
        return {"action": "handoff.no_contact", "lead": lead_id}
    contact_id = int(contacts[0]["id"])

    kommo_existing = _already_in_kommo(ctx, contact_id, lead_id)
    if kommo_existing:
        log_decision(ctx, "handoff.already_kommo", lead=lead_id, assembly=kommo_existing)
        ctx.s.add(Handoff(source_lead_id=lead_id, assembly_lead_id=kommo_existing,
                          contact_id=contact_id, shadow=False))
        ctx.s.flush()
        return {"action": "handoff.already", "lead": lead_id, "assembly_lead": kommo_existing}

    if not settings.enable_handoff or ctx.shadow:
        log_decision(ctx, "handoff.shadow", lead=lead_id, contact=contact_id,
                     shadow=ctx.shadow, enabled=settings.enable_handoff)
        return {"action": "handoff.would_create", "lead": lead_id, "contact": contact_id}

    if _rate_limited(ctx):
        log_decision(ctx, "handoff.rate_limited", lead=lead_id, contact=contact_id)
        return {"action": "handoff.rate_limited", "lead": lead_id}

    assembly_id = _create_assembly_lead(ctx, lead, contact_id)
    if not assembly_id:
        log_decision(ctx, "handoff.create_failed", lead=lead_id, contact=contact_id)
        return {"action": "handoff.create_failed", "lead": lead_id}

    ctx.s.add(Handoff(source_lead_id=lead_id, assembly_lead_id=assembly_id,
                      contact_id=contact_id, shadow=False))
    ctx.s.flush()

    if settings.handoff_copy_notes:
        _copy_history(ctx, lead_id, assembly_id)

    add_note(ctx, "deal", lead_id, f"Сборка: сделка #{assembly_id}.")
    add_note(ctx, "deal", assembly_id, f"Продажа: сделка #{lead_id}.")
    add_tags(ctx, "deal", assembly_id, {"_embedded": {"tags": []}}, [settings.handoff_tag])

    log_decision(ctx, "handoff.created", source=lead_id, assembly=assembly_id, contact=contact_id)
    return {"action": "handoff.created", "source_lead": lead_id,
            "assembly_lead": assembly_id, "contact": contact_id}
