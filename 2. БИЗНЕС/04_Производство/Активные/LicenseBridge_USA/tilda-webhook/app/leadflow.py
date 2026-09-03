"""Лид-машина: заявка → WhatsApp → AI-звонок Pleep → SMS.

Порядок работы (схема согласована 2026-08-10):

1. Make кладёт заявку Meta в «Новую заявку». Хаб на `add_lead` дописывает в сделку
   `wa_variant` (какой из вариантов текста слать) и окно звонка по местному времени
   клиента — Salesbot берёт готовые значения, а не считает их сам.
2. Salesbot пишет в WhatsApp сразу (это ответ на заявку клиента, а не рассылка) и
   ждёт ответа. Молчание → вебхук в `/internal/leadflow/ai-call`.
3. Хаб проверяет местное время клиента. В тихие часы звонок откладывается до утра
   (сообщения при этом не останавливаются), иначе Asterisk соединяет клиента с
   голосовым агентом Pleep.
4. Итог разговора Pleep присылает в `/internal/pleep/outcome`: квалифицирован —
   этап «В работе» и задача менеджеру; отказ или недозвон — SMS-бот RingCentral;
   стоп-слова — тег «не звонить» и снятие с автоматики.

Всё, что меняет Kommo, живёт под флагом `ENABLE_LEADFLOW` — как телефония, вне
лестницы авто-раскатки: включается руками после сквозного прогона.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.actions import Ctx, add_tags, log_decision
from app.config import CLOSED_STATUS_IDS, settings
from app.identity import contact_phones, find_contacts_by_phone, normalize_phone
from app.models import AiCall
from app.tasks import create_task

log = logging.getLogger("leadflow")

# что Pleep может прислать в поле outcome → наши четыре ветки
OUTCOME_ALIASES: dict[str, str] = {
    "qualified": "qualified", "interested": "qualified", "success": "qualified",
    "callback": "callback", "call_back": "callback", "later": "callback",
    "refused": "refused", "declined": "refused", "not_interested": "refused",
    "no_answer": "no_answer", "noanswer": "no_answer", "voicemail": "no_answer",
    "busy": "no_answer", "failed": "no_answer",
    "do_not_call": "do_not_call", "dnc": "do_not_call", "stop": "do_not_call",
}


# ── вариант текста и окно звонка ──
def wa_variant(lead_id: int) -> str:
    """Какой из вариантов первого сообщения отправить.

    Считаем от id сделки, а не от счётчика в БД: id в Kommo сквозной, варианты
    распределяются ровно, а повтор вебхука по той же сделке не сдвигает очередь и
    не приводит к отправке второго, «нового» текста."""
    count = max(1, settings.wa_variant_count)
    return str(lead_id % count + 1)


def client_timezone(phone: str) -> str:
    """Часовой пояс клиента по коду номера. Не определился — часы офиса."""
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone

        zones = pn_timezone.time_zones_for_number(phonenumbers.parse(phone, "US"))
    except Exception as exc:  # noqa: BLE001 — кривой номер не должен ронять поток
        log.debug("timezone lookup failed for %s: %s", phone, exc)
        return settings.call_window_default_tz
    for zone in zones:
        if zone and zone != "Etc/Unknown":
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                continue
            return zone
    return settings.call_window_default_tz


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.call_window_default_tz)


def call_window_label(tz: str) -> str:
    return (f"{settings.call_window_start_hour:02d}:00–"
            f"{settings.call_window_end_hour:02d}:00 {tz}")


def in_call_window(tz: str, now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(_zone(tz))
    return settings.call_window_start_hour <= local.hour < settings.call_window_end_hour


def next_window_start(tz: str, now: datetime | None = None) -> datetime:
    """Ближайшее время в UTC, когда клиенту снова можно звонить."""
    zone = _zone(tz)
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = local.replace(hour=settings.call_window_start_hour, minute=0,
                          second=0, microsecond=0)
    if local >= start:
        start += timedelta(days=1)
    return start.astimezone(timezone.utc)


# ── работа с Kommo ──
def _patch_lead(ctx: Ctx, lead_id: int, payload: dict[str, Any]) -> bool:
    if not settings.enable_leadflow:
        log_decision(ctx, "leadflow.disabled", lead=lead_id, payload=payload)
        return False
    ctx.client.update_lead(lead_id, payload)
    return True


def enrich_lead(ctx: Ctx, lead: dict[str, Any], phone: str) -> dict[str, Any]:
    """Дописать в сделку вариант текста и окно звонка (зовётся на add_lead)."""
    lead_id = int(lead["id"])
    if lead.get("pipeline_id") != settings.pipeline_id:
        return {"action": "leadflow.skip_pipeline", "lead": lead_id}
    variant = wa_variant(lead_id)
    tz = client_timezone(phone) if phone else settings.call_window_default_tz
    window = call_window_label(tz)
    written = _patch_lead(ctx, lead_id, {"custom_fields_values": [
        {"field_id": settings.field_wa_variant, "values": [{"value": variant}]},
        {"field_id": settings.field_call_window, "values": [{"value": window}]},
    ]})
    log_decision(ctx, "leadflow.enriched", lead=lead_id, variant=variant,
                 tz=tz, written=written)
    return {"action": "leadflow.enriched", "lead": lead_id, "variant": variant,
            "tz": tz, "written": written}


def first_touch_text(lead: dict[str, Any], variant: str) -> str:
    """Текст первого сообщения. Пусто — значит вариант не заполнен в `.env`."""
    template = settings.wa_template_map.get(variant, "")
    if not template:
        return ""
    name = ""
    for contact in ((lead.get("_embedded") or {}).get("contacts")) or []:
        name = str(contact.get("name") or "").strip()
        if name:
            break
    if not name:
        name = str(lead.get("name") or "").strip()
    # только имя, без фамилии: «Hi John» вместо «Hi John Smith»
    return template.replace("{name}", name.split()[0] if name else "")


def send_first_touch(ctx: Ctx, lead: dict[str, Any], phone: str) -> dict[str, Any]:
    """Написать клиенту первым в WhatsApp (зовётся на add_lead).

    Раньше это делал Salesbot внутри Kommo: бота выключили в UI, и заявки молча
    остались без ответа (26.08–03.09.2026). Теперь пишет хаб, и каждая попытка
    видна в журнале решений.

    Тихие часы сообщение не задерживают — это ответ на заявку клиента, а не
    рассылка. Тихие часы действуют только на звонки."""
    from app.models import FirstTouch
    from app.wazzup import send_text

    lead_id = int(lead["id"])
    if lead.get("pipeline_id") != settings.pipeline_id:
        return {"action": "first_touch.skip_pipeline", "lead": lead_id}
    phone = normalize_phone(phone)
    if not phone:
        log_decision(ctx, "first_touch.no_phone", lead=lead_id)
        return {"action": "first_touch.no_phone", "lead": lead_id}

    row = ctx.s.scalar(select(FirstTouch).where(FirstTouch.lead_id == lead_id))
    if row is not None and row.status == "sent":
        log_decision(ctx, "first_touch.already", lead=lead_id, phone=phone)
        return {"action": "first_touch.already", "lead": lead_id}

    variant = wa_variant(lead_id)
    text = first_touch_text(lead, variant)

    reason = ""
    if not settings.enable_wazzup_first_touch:
        reason = "disabled"
    elif not text:
        reason = "no_template"
    elif settings.wazzup_test_phone and phone != normalize_phone(settings.wazzup_test_phone):
        # прогон идёт на боевом хабе: живым лидам в это время не пишем
        reason = "test_mode"
    if reason:
        log_decision(ctx, f"first_touch.{reason}", lead=lead_id, phone=phone,
                     variant=variant)
        return {"action": f"first_touch.{reason}", "lead": lead_id, "variant": variant}

    if row is None:
        row = FirstTouch(lead_id=lead_id, contact_id=_contact_id(lead), phone=phone)
        ctx.s.add(row)
    row.phone = phone
    row.variant = variant
    try:
        message_id = send_text(phone, text)
    except Exception as exc:  # noqa: BLE001 — заявка важнее отправки сообщения
        row.status = "failed"
        row.last_error = str(exc)[:500]
        ctx.s.flush()
        log_decision(ctx, "first_touch.failed", lead=lead_id, phone=phone,
                     variant=variant, error=str(exc)[:200])
        return {"action": "first_touch.failed", "lead": lead_id, "error": str(exc)[:200]}
    row.status = "sent"
    row.message_id = message_id or None
    row.last_error = None
    ctx.s.flush()
    log_decision(ctx, "first_touch.sent", lead=lead_id, phone=phone, variant=variant,
                 message=message_id)
    return {"action": "first_touch.sent", "lead": lead_id, "variant": variant,
            "message": message_id}


def _open_lead_by_phone(ctx: Ctx, phone: str) -> dict[str, Any] | None:
    from app.dedup_deals import load_leads

    for contact in find_contacts_by_phone(ctx.client, phone):
        leads = [l for l in load_leads(ctx.client, int(contact["id"]))
                 if l.get("closed_at") in (None, 0)
                 and l.get("status_id") not in CLOSED_STATUS_IDS
                 and l.get("pipeline_id") == settings.pipeline_id]
        if leads:
            leads.sort(key=lambda l: l.get("created_at") or 0)
            return leads[0]
    return None


def _resolve(ctx: Ctx, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Сделка и телефон по тому, что прислал Salesbot или Pleep."""
    lead_id = str(payload.get("lead_id") or payload.get("entity_id") or "").strip()
    phone = normalize_phone(payload.get("phone") or payload.get("caller") or "")
    lead: dict[str, Any] | None = None
    if lead_id.isdigit():
        lead = ctx.client.get_lead(int(lead_id), with_="contacts")
    if lead is None and phone:
        lead = _open_lead_by_phone(ctx, phone)
    if lead and not phone:
        for c in ((lead.get("_embedded") or {}).get("contacts")) or []:
            full = ctx.client.get_contact(int(c["id"]), with_="leads")
            phones = contact_phones(full or {})
            if phones:
                phone = phones[0]
                break
    return lead, phone


def _contact_id(lead: dict[str, Any]) -> int | None:
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    return int(contacts[0]["id"]) if contacts else None


# ── AI-звонок ──
def _originate(ctx: Ctx, row: AiCall) -> dict[str, Any]:
    from app.ami import AmiError, originate

    row.attempts += 1
    if row.attempts > settings.ai_call_max_attempts:
        row.status = "failed"
        log_decision(ctx, "ai_call.max_attempts", lead=row.lead_id, attempts=row.attempts)
        return {"action": "ai_call.max_attempts", "lead": row.lead_id}
    if not settings.enable_leadflow:
        row.status = "skipped"
        log_decision(ctx, "ai_call.disabled", lead=row.lead_id, phone=row.phone)
        return {"action": "ai_call.disabled", "lead": row.lead_id}
    try:
        action_id = originate(row.phone, variables={
            "LB_AI_LEAD": row.lead_id,
            "LB_AI_PHONE": row.phone,
            "LB_AI_NUMBER": settings.pleep_number,
        })
    except AmiError as exc:
        row.status = "failed"
        row.last_error = str(exc)[:500]
        log_decision(ctx, "ai_call.failed", lead=row.lead_id, phone=row.phone,
                     error=str(exc)[:200])
        return {"action": "ai_call.failed", "lead": row.lead_id, "error": str(exc)[:200]}
    row.status = "dialing"
    row.scheduled_at = None
    row.last_error = None
    ctx.s.flush()
    log_decision(ctx, "ai_call.started", lead=row.lead_id, phone=row.phone,
                 attempt=row.attempts, action_id=action_id)
    return {"action": "ai_call.started", "lead": row.lead_id, "phone": row.phone}


def handle_ai_call(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Salesbot не дождался ответа в WhatsApp — соединяем клиента с Pleep."""
    lead, phone = _resolve(ctx, payload)
    if not lead:
        log_decision(ctx, "ai_call.no_lead", payload=payload)
        return {"action": "ai_call.no_lead"}
    lead_id = int(lead["id"])
    if not phone:
        log_decision(ctx, "ai_call.no_phone", lead=lead_id)
        return {"action": "ai_call.no_phone", "lead": lead_id}
    if settings.tag_do_not_call in _tags(lead):
        log_decision(ctx, "ai_call.do_not_call", lead=lead_id)
        return {"action": "ai_call.do_not_call", "lead": lead_id}

    row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead_id))
    if row is None:
        row = AiCall(lead_id=lead_id, contact_id=_contact_id(lead), phone=phone)
        ctx.s.add(row)
        ctx.s.flush()
    elif row.status in ("dialing", "done"):
        # повтор вебхука Salesbot: второй звонок по той же заявке — это уже обзвон
        log_decision(ctx, "ai_call.already", lead=lead_id, status=row.status)
        return {"action": "ai_call.already", "lead": lead_id, "status": row.status}
    row.phone = phone
    row.tz = client_timezone(phone)

    if not in_call_window(row.tz):
        row.status = "scheduled"
        row.scheduled_at = next_window_start(row.tz)
        ctx.s.flush()
        log_decision(ctx, "ai_call.deferred", lead=lead_id, phone=phone, tz=row.tz,
                     until=row.scheduled_at.isoformat())
        return {"action": "ai_call.deferred", "lead": lead_id, "tz": row.tz,
                "until": row.scheduled_at.isoformat()}
    return _originate(ctx, row)


def silent_after_first_touch(ctx: Ctx, now: datetime | None = None) -> list[int]:
    """Сделки, где мы написали первыми, а клиент молчит дольше положенного.

    Раньше молчание отслеживал Salesbot и сам звал `/internal/leadflow/ai-call`.
    Первое сообщение отправляет хаб, значит и молчание считает он, иначе цепочка
    обрывается на сообщении и до голосового агента дело не доходит.

    Ответ клиента ищем в переписке: одного факта отправки мало, клиент мог
    ответить в другом канале того же чата."""
    from app.chat_events import fetch as fetch_chats
    from app.models import AiCall, FirstTouch

    moment = now or datetime.now(timezone.utc)
    oldest = moment - timedelta(hours=settings.leadflow_followup_max_hours)
    ready = moment - timedelta(minutes=settings.leadflow_silence_min)
    rows = list(ctx.s.scalars(
        select(FirstTouch)
        .where(FirstTouch.status == "sent",
               FirstTouch.created_at <= ready,
               FirstTouch.created_at >= oldest)
        .order_by(FirstTouch.created_at.asc())
        .limit(50)
    ))
    if not rows:
        return []
    already = set(ctx.s.scalars(
        select(AiCall.lead_id).where(AiCall.lead_id.in_([r.lead_id for r in rows]))
    ).all())
    pending = [r for r in rows if r.lead_id not in already]
    if not pending:
        return []
    chats = fetch_chats(ctx.client, int(oldest.timestamp()))
    out = []
    for row in pending:
        stats = chats.get(row.lead_id)
        if stats and stats.incoming:
            continue  # клиент ответил, дальше работает человек
        out.append(row.lead_id)
    return out


def run_silence_followups(ctx: Ctx, now: datetime | None = None) -> list[dict[str, Any]]:
    """Клиент не ответил на первое сообщение — соединяем его с голосовым агентом."""
    out: list[dict[str, Any]] = []
    for lead_id in silent_after_first_touch(ctx, now):
        out.append(handle_ai_call(ctx, {"lead_id": lead_id}))
    return out


def due_calls(s, now: datetime | None = None) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    return list(s.scalars(
        select(AiCall.lead_id)
        .where(AiCall.status == "scheduled", AiCall.scheduled_at <= moment)
        .order_by(AiCall.scheduled_at.asc())
        .limit(20)
    ).all())


def run_due_calls(ctx: Ctx, now: datetime | None = None) -> list[dict[str, Any]]:
    """Отложенные на утро звонки, у которых наступило окно (зовётся из worker)."""
    out: list[dict[str, Any]] = []
    for lead_id in due_calls(ctx.s, now):
        row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead_id))
        if row is None:
            continue
        out.append(_originate(ctx, row))
    return out


# ── итог разговора ──
def _tags(lead: dict[str, Any]) -> list[str]:
    from app.actions import current_tags

    return current_tags(lead)


def _run_sms_bot(ctx: Ctx, lead_id: int, phone: str = "") -> bool:
    """Запуск SMS-бота RingCentral. Одно SMS на телефон, даже если сделок две."""
    if not settings.sms_bot_id:
        log_decision(ctx, "sms.no_bot", lead=lead_id)
        return False
    if not settings.enable_leadflow:
        log_decision(ctx, "sms.disabled", lead=lead_id, bot=settings.sms_bot_id)
        return False
    phone = normalize_phone(phone)
    if phone:
        others = [
            r for r in ctx.s.scalars(
                select(AiCall).where(
                    AiCall.phone == phone,
                    AiCall.outcome.in_(("refused", "no_answer")),
                )
            )
            if r.lead_id != lead_id
        ]
        if others:
            log_decision(ctx, "sms.duplicate_phone", lead=lead_id, phone=phone,
                         other=others[0].lead_id)
            return False
    ctx.client.post("/salesbot/run", [{"bot_id": settings.sms_bot_id,
                                       "entity_id": lead_id, "entity_type": 2}])
    log_decision(ctx, "sms.started", lead=lead_id, bot=settings.sms_bot_id)
    return True


def _note(ctx: Ctx, lead_id: int, text: str) -> None:
    if not settings.enable_leadflow:
        log_decision(ctx, "leadflow.note.skipped", lead=lead_id, text=text[:200])
        return
    ctx.client.add_note("leads", lead_id, text)


def _owner(ctx: Ctx, lead: dict[str, Any]) -> int:
    return int(lead.get("responsible_user_id") or settings.default_sales_owner_id)


def handle_pleep_outcome(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Итог разговора голосового агента: этап, задача, SMS или снятие с автоматики."""
    lead, phone = _resolve(ctx, payload)
    raw = str(payload.get("outcome") or payload.get("result") or "").strip().lower()
    outcome = OUTCOME_ALIASES.get(raw, "")
    summary = str(payload.get("summary") or payload.get("transcript") or "").strip()
    if not lead:
        log_decision(ctx, "pleep.no_lead", outcome=raw, phone=phone)
        return {"action": "pleep.no_lead", "outcome": raw}
    lead_id = int(lead["id"])
    if not outcome:
        _note(ctx, lead_id, f"AI-звонок Pleep: непонятный итог «{raw or '—'}».\n{summary}".strip())
        log_decision(ctx, "pleep.unknown_outcome", lead=lead_id, outcome=raw)
        return {"action": "pleep.unknown_outcome", "lead": lead_id, "outcome": raw}

    # строки может не быть: агент отвечает и на входящие в нерабочее время, такой
    # разговор мы не инициировали. Заводим её здесь, чтобы повтор итога (Pleep
    # ретраит HTTP tool) не переставил этап и не отправил второе SMS.
    row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead_id))
    if row is None:
        row = AiCall(lead_id=lead_id, contact_id=_contact_id(lead), phone=phone or "")
        ctx.s.add(row)
    elif row.outcome:
        log_decision(ctx, "pleep.duplicate", lead=lead_id, outcome=outcome)
        return {"action": "pleep.duplicate", "lead": lead_id, "outcome": row.outcome}
    row.outcome = outcome
    row.status = "done"
    ctx.s.flush()

    owner = _owner(ctx, lead)
    head = {"qualified": "AI-звонок Pleep: клиент квалифицирован.",
            "callback": "AI-звонок Pleep: клиент просил перезвонить.",
            "refused": "AI-звонок Pleep: клиент отказался говорить.",
            "no_answer": "AI-звонок Pleep: клиент не ответил.",
            "do_not_call": "AI-звонок Pleep: клиент попросил больше не звонить."}[outcome]
    _note(ctx, lead_id, f"{head}\n{summary}".strip())

    sms = False
    task_id = None
    if outcome == "qualified":
        if _patch_lead(ctx, lead_id, {"status_id": settings.status_in_work}):
            log_decision(ctx, "leadflow.stage", lead=lead_id, status=settings.status_in_work)
        task_id = create_task(ctx, "deal", lead_id,
                              f"Клиент квалифицирован AI-звонком, связаться: {phone}",
                              owner, minutes=settings.leadflow_task_minutes)
    elif outcome == "callback":
        task_id = create_task(ctx, "deal", lead_id,
                              f"Клиент просил перезвонить (AI-звонок): {phone}",
                              owner, minutes=settings.leadflow_task_minutes)
    elif outcome in ("refused", "no_answer"):
        sms = _run_sms_bot(ctx, lead_id, phone or row.phone or "")
        if not sms:
            task_id = create_task(ctx, "deal", lead_id,
                                  f"AI-звонок без результата, написать клиенту: {phone}",
                                  owner, minutes=settings.leadflow_task_minutes)
    else:  # do_not_call
        add_tags(ctx, "deal", lead_id, lead, [settings.tag_do_not_call])

    log_decision(ctx, "pleep.outcome", lead=lead_id, outcome=outcome, sms=sms, task=task_id)
    return {"action": "pleep.outcome", "lead": lead_id, "outcome": outcome,
            "sms": sms, "task": task_id}


def on_ai_call_finished(ctx: Ctx, phone: str, answered: bool,
                        lead_id: int | None) -> dict[str, Any]:
    """Мост положил трубку. Клиент не ответил — Pleep итог не пришлёт, поэтому
    ветку «недозвон» закрывает сам хаб по отчёту Asterisk."""
    row = None
    if lead_id:
        row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == int(lead_id)))
    if row is None and phone:
        row = ctx.s.scalars(
            select(AiCall).where(AiCall.phone == phone, AiCall.status == "dialing")
            .order_by(AiCall.id.desc())
        ).first()
    if row is None:
        return {"action": "ai_call.unknown", "phone": phone}
    if answered:
        # разговор состоялся: итог придёт из Pleep, ждём его
        row.status = "done"
        ctx.s.flush()
        return {"action": "ai_call.answered", "lead": row.lead_id}
    if row.outcome:
        return {"action": "ai_call.already_closed", "lead": row.lead_id}
    return handle_pleep_outcome(ctx, {"lead_id": row.lead_id, "phone": row.phone,
                                      "outcome": "no_answer",
                                      "summary": "Клиент не взял трубку."})
