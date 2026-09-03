"""Worker: разбирает inbox, держит advisory-лок по телефону, делает всю работу с
Kommo. Вебхуки только кладут событие и отвечают 202."""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions import Ctx, log_decision
from app.assignment import assign_new_lead
from app.config import settings
from app.db import phone_lock, session_scope
from app.dedup_contacts import resolve as resolve_contacts
from app.dedup_deals import load_leads, resolve as resolve_deals
from app.handoff import run_handoff
from app.identity import (
    contact_phones,
    find_contacts_by_phone,
    find_contacts_by_secondary,
    secondary_keys,
)
from app.intake import process_intake
from app.kommo.client import KommoClient, KommoError
from app.models import InboxEvent
from app.rollout import current_flags, describe, maybe_promote

log = logging.getLogger("worker")


# ── резолв контакта клиента (dedup) ──
def _resolve_contact(ctx: Ctx, contact: dict[str, Any]) -> tuple[int | None, bool, list[int]]:
    """Возвращает (итоговый contact_id, has_phone, группа карточек одного клиента).

    Группа — все найденные карточки, включая несклеенные: антидублю сделок нужны
    именно они, иначе дубль сделки на несклеенной карточке остаётся незамеченным."""
    phones = contact_phones(contact)
    if phones:
        # у карточки бывает несколько номеров (рабочий/личный), и дубль может висеть
        # на любом из них. Номер, по которому пришло событие, проверяем первым.
        for phone in sorted(phones, key=lambda p: p != ctx.phone):
            candidates = find_contacts_by_phone(ctx.client, phone)
            if len(candidates) > 1:
                res = resolve_contacts(ctx, candidates, has_phone=True)
                return res.get("contact_id"), True, res.get("group") or []
        return int(contact["id"]), True, [int(contact["id"])]
    # без телефона — по вторичным ключам
    for key, value in secondary_keys(contact).items():
        candidates = find_contacts_by_secondary(ctx.client, key, value)
        if len(candidates) > 1:
            res = resolve_contacts(ctx, candidates, has_phone=False)
            return res.get("contact_id"), False, res.get("group") or []
    return int(contact["id"]), False, [int(contact["id"])]


def _phone_group(ctx: Ctx, contact_id: int) -> list[int]:
    """Карточки одного номера, только чтение (без склейки контактов).

    Нужен там, где склейку контактов запускать не хотим (смена этапа сделки), но
    дубли сделок искать надо по всем карточкам клиента."""
    contact = ctx.client.get_contact(contact_id, with_="leads")
    ids = {contact_id}
    for phone in contact_phones(contact or {}):
        for c in find_contacts_by_phone(ctx.client, phone):
            ids.add(int(c["id"]))
    return sorted(ids)


# ── обработчики Kommo-вебхуков ──
def handle_kommo_add_lead(ctx: Ctx, lead_id: int) -> dict[str, Any]:
    lead = ctx.client.get_lead(lead_id, with_="contacts")
    if not lead:
        return {"action": "lead_gone", "lead": lead_id}
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    if not contacts:
        log_decision(ctx, "add_lead.no_contact", lead=lead_id)
        return {"action": "no_contact", "lead": lead_id}
    primary = None
    for c in contacts:
        full = ctx.client.get_contact(int(c["id"]), with_="leads")
        if full and contact_phones(full):
            primary = full
            break
    if primary is None:
        primary = ctx.client.get_contact(int(contacts[0]["id"]), with_="leads")
    if not primary:
        return {"action": "no_contact", "lead": lead_id}

    contact_id, _, group = _resolve_contact(ctx, primary)
    if not contact_id:
        return {"action": "shadow_no_contact", "lead": lead_id}

    resolve_deals(ctx, int(contact_id), new_lead_id=lead_id, trigger="add_lead",
                  group_contact_ids=group)

    # назначить нового лида (если он ещё существует после возможной свёртки)
    leads = load_leads(ctx.client, group or int(contact_id))
    this_lead = next((l for l in leads if int(l["id"]) == int(lead_id)), None)
    if this_lead:
        assign_new_lead(ctx, this_lead, leads)
        phones = contact_phones(primary)
        # лид-машина: Salesbot берёт из сделки готовый вариант текста и окно звонка
        if settings.enable_leadflow:
            from app.leadflow import enrich_lead
            enrich_lead(ctx, this_lead, phones[0] if phones else "")
        # Первое сообщение отправляет хаб, а не Salesbot, поэтому у него свой
        # флаг: писать клиентам можно и до включения остальной лид-машины.
        from app.leadflow import send_first_touch
        send_first_touch(ctx, this_lead, phones[0] if phones else "")

    # Алерт уходит последним и своей ошибкой обработку не роняет: заявка уже
    # разложена, уведомление — сверху.
    alerted = False
    try:
        from app.alerts import new_lead as alert_new_lead

        fresh = ctx.client.get_lead(lead_id, with_="contacts") or this_lead or {}
        if fresh:
            phones = contact_phones(primary)
            alerted = alert_new_lead(ctx.client, fresh, phones[0] if phones else "")
    except Exception as exc:  # noqa: BLE001
        log.warning("new lead alert failed for %s: %s", lead_id, exc)
    return {"action": "add_lead_done", "lead": lead_id, "contact": contact_id,
            "alerted": alerted}


def handle_kommo_update_contact(ctx: Ctx, contact_id: int) -> dict[str, Any]:
    contact = ctx.client.get_contact(contact_id, with_="leads")
    if not contact:
        return {"action": "contact_gone", "contact": contact_id}
    final_id, _, group = _resolve_contact(ctx, contact)
    if final_id:
        resolve_deals(ctx, int(final_id), None, trigger="update_contact",
                      group_contact_ids=group)
    return {"action": "update_contact_done", "contact": contact_id, "final": final_id}


def handle_kommo_status_lead(ctx: Ctx, lead_id: int, status_id: int | None) -> dict[str, Any]:
    lead = ctx.client.get_lead(lead_id, with_="contacts")
    if not lead:
        return {"action": "lead_gone", "lead": lead_id}
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    if not contacts:
        return {"action": "no_contact", "lead": lead_id}
    contact_id = int(contacts[0]["id"])
    # появление второй открытой сделки / вход в «Новая заявка» → антидубль + распределение
    group = _phone_group(ctx, contact_id)
    resolve_deals(ctx, contact_id, new_lead_id=lead_id, trigger="status_lead",
                  group_contact_ids=group)
    if status_id == settings.status_new:
        leads = load_leads(ctx.client, group or contact_id)
        this_lead = next((l for l in leads if int(l["id"]) == int(lead_id)), None)
        if this_lead:
            assign_new_lead(ctx, this_lead, leads)
    # won в Pipeline → новая сделка в «Сборке» (см. app/handoff.py); на любом
    # другом статусе/воронке run_handoff молча возвращает handoff.skip_*.
    handoff_result = run_handoff(ctx, lead_id, lead=lead)
    return {"action": "status_lead_done", "lead": lead_id, "handoff": handoff_result["action"]}


# ── диспетчер одного события ──
def process_event(ctx: Ctx, ev: InboxEvent) -> dict[str, Any]:
    src, typ, payload = ev.source, ev.event_type, ev.payload or {}
    if src in ("tilda", "pleep", "intake", "facebook"):
        return process_intake(ctx, payload)
    if src == "kommo":
        entity_id = int(payload.get("entity_id") or 0)
        if typ == "add_lead":
            return handle_kommo_add_lead(ctx, entity_id)
        if typ == "update_contact":
            return handle_kommo_update_contact(ctx, entity_id)
        if typ == "status_lead":
            return handle_kommo_status_lead(ctx, entity_id, payload.get("status_id"))
    if src == "asterisk":
        from app.telephony import handle_call
        return handle_call(ctx, typ, payload)
    if src == "leadflow":
        from app.leadflow import handle_ai_call, handle_pleep_outcome
        if typ == "ai_call":
            return handle_ai_call(ctx, payload)
        if typ == "pleep_outcome":
            return handle_pleep_outcome(ctx, payload)
    if src == "scanner":
        from app.scanner import handle_scanner_event
        return handle_scanner_event(ctx, payload)
    log_decision(ctx, "unknown_event", source=src, type=typ)
    return {"action": "unknown", "source": src, "type": typ}


def _claim_batch(s: Session, limit: int = 10) -> list[InboxEvent]:
    rows = s.scalars(
        select(InboxEvent)
        .where(InboxEvent.status == "pending",
               InboxEvent.attempts < settings.worker_max_attempts)
        .order_by(InboxEvent.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    for r in rows:
        r.status = "processing"
    return rows


def process_one(client: KommoClient, event_id: int) -> None:
    with session_scope() as s:
        ev = s.get(InboxEvent, event_id)
        if not ev or ev.status not in ("processing", "pending"):
            return
        ev.attempts += 1
        flags = current_flags(s)
        with phone_lock(s, ev.phone):
            ctx = Ctx(client=client, session=s, inbox_id=ev.id, phone=ev.phone,
                      shadow=flags.shadow, flags=flags)
            try:
                result = process_event(ctx, ev)
                ev.status = "done"
                ev.last_error = None
                log_decision(ctx, "event.done", result=result)
            except KommoError as exc:
                ev.last_error = str(exc)
                ev.status = "deadletter" if ev.attempts >= settings.worker_max_attempts else "pending"
                log.warning("event %s failed (kommo): %s", ev.id, exc)
            except Exception as exc:  # noqa: BLE001
                ev.last_error = repr(exc)
                ev.status = "deadletter" if ev.attempts >= settings.worker_max_attempts else "pending"
                log.exception("event %s failed", ev.id)


def run_loop() -> None:
    from app.db import init_db
    from app.merge_tags import ensure_pool

    init_db()
    with session_scope() as s:
        ensure_pool(s)

    # preflight: вне shadow неверная конфигурация не должна запускать мутации.
    from app.preflight import run_preflight
    with session_scope() as s:
        start_flags = current_flags(s)
    problems = run_preflight()
    if problems and not start_flags.shadow:
        raise SystemExit("preflight failed (mutations disabled): " + "; ".join(problems))
    if problems:
        log.warning("preflight issues (shadow, продолжаем): %s", problems)

    client = KommoClient()
    try:
        from app.scanner import run_scheduler
        run_scheduler()
    except Exception as exc:  # scheduler не критичен для основного цикла
        log.warning("scheduler start failed: %s", exc)
    with session_scope() as s:
        log.info("worker started rollout=%s", describe(s))
    next_rollout_check = 0.0
    next_ai_sweep = 0.0
    next_owner_sweep = 0.0
    # первую проверку не делаем сразу: сразу после старта очередь и активность
    # выглядят пусто, и алерт был бы ложным
    next_monitor = time.monotonic() + settings.monitor_interval_sec
    try:
        while True:
            # наблюдение: о поломке должны узнавать мы, а не клиент
            if time.monotonic() >= next_monitor:
                next_monitor = time.monotonic() + settings.monitor_interval_sec
                try:
                    from app.monitor import run_checks
                    with session_scope() as s:
                        fired = run_checks(s, client)
                    if fired:
                        log.warning("monitor alerts: %s", fired)
                except Exception as exc:  # наблюдатель не мешает обработке
                    log.warning("monitor sweep failed: %s", exc)
            # авто-раскатка: этап двигается сам, если выдержано время и нет сбоев
            if time.monotonic() >= next_rollout_check:
                next_rollout_check = time.monotonic() + settings.rollout_check_interval_sec
                try:
                    with session_scope() as s:
                        maybe_promote(s)
                except Exception as exc:  # раскатка не должна ронять обработку
                    log.warning("rollout check failed: %s", exc)
            # карточки продаж, осевшие на том, кто продажи не ведёт: событие
            # создания их не поймает, если сделку завёл сторонний сценарий
            if time.monotonic() >= next_owner_sweep:
                next_owner_sweep = time.monotonic() + settings.owner_sweep_interval_sec
                try:
                    from app.assignment import sweep_non_sales_owners
                    with session_scope() as s:
                        flags = current_flags(s)
                        moved = sweep_non_sales_owners(
                            Ctx(client=client, session=s, inbox_id=None, phone=None,
                                shadow=flags.shadow, flags=flags))
                    if moved:
                        log.warning("leads moved to sales owner: %s", moved)
                except Exception as exc:  # подметание не мешает очереди
                    log.warning("owner sweep failed: %s", exc)
            # отложенные AI-звонки: клиент был в тихих часах, ждём его утра
            if settings.enable_leadflow and time.monotonic() >= next_ai_sweep:
                next_ai_sweep = time.monotonic() + settings.ai_call_sweep_interval_sec
                try:
                    from app.leadflow import run_due_calls
                    with session_scope() as s:
                        flags = current_flags(s)
                        due = run_due_calls(Ctx(client=client, session=s, inbox_id=None,
                                                phone=None, shadow=flags.shadow, flags=flags))
                    if due:
                        log.info("ai calls fired: %s", due)
                except Exception as exc:  # звонки не должны ронять очередь событий
                    log.warning("ai call sweep failed: %s", exc)
            with session_scope() as s:
                batch = _claim_batch(s)
                ids = [ev.id for ev in batch]
            if not ids:
                time.sleep(settings.worker_poll_interval_sec)
                continue
            for eid in ids:
                process_one(client, eid)
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_loop()
