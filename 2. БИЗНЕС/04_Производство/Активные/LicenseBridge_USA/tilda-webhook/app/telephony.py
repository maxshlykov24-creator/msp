"""Телефония: очередь дозвона для Asterisk и журнал звонков в Kommo.

Диалплан на 159.65.97.156 спрашивает у хаба, кому звонить (`/internal/call/route`),
а по итогам присылает событие: никто не ответил (`/internal/call/missed`) или
разговор завершён (`/internal/call/finished`). Вебхуки только кладут событие в
inbox, всю работу с Kommo делает worker — как и для остальных каналов.

Записи разговоров лежат на Asterisk; наружу их отдаёт хаб по подписанной ссылке
(`/rec/{подпись}/{файл}`), чтобы каталог с разговорами клиентов не висел в
интернете открытым.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import re
import time
from typing import Any

from sqlalchemy import select

from app.actions import Ctx, log_decision
from app.config import CLOSED_STATUS_IDS, settings
from app.identity import find_contacts_by_phone, normalize_phone
from app.kommo.client import KommoError
from app.models import CallEvent
from app.resolver import resolve_responsible
from app.tasks import create_task

log = logging.getLogger("telephony")

# коды Kommo для примечания-звонка (сверено с примечаниями боевого аккаунта)
CALL_ANSWERED = 4
CALL_MISSED = 6

REC_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]{4,160}$")


# ── очередь дозвона ──
def ring_order(client: Any, phone: str, did: str = "") -> tuple[list[str], dict[str, Any]]:
    """Кому звонить и в каком порядке.

    Сначала ответственный клиента (если у него есть добавочный и он не из
    клиентского отдела), затем владелец набранной линии, затем остальной круг
    продаж. Неизвестный номер → просто круг продаж.

    Исключение — клиент в воронке «Сборка»: он уже оплатил, его ведёт клиентский
    отдел, и звонок начинается с него. Круг продаж остаётся позади как запас,
    чтобы звонок не пропал, если в клиентском отделе никого нет."""
    resolved: dict[str, Any] = {"found": False}
    if phone:
        try:
            resolved = resolve_responsible(client, phone)
        except Exception as exc:  # телефония не должна падать из-за Kommo
            log.warning("resolve failed for %s: %s", phone, exc)
            resolved = {"found": False, "reason": "resolve_error"}

    order: list[str] = []
    uid = resolved.get("responsible_user_id")
    in_assembly = resolved.get("pipeline_id") == settings.assembly_pipeline_id
    if uid and (in_assembly or int(uid) not in settings.client_dept_owner_id_set):
        ext = settings.user_to_ext.get(int(uid))
        if ext:
            order.append(ext)
    if in_assembly:
        service_ext = settings.user_to_ext.get(settings.telephony_service_owner_id)
        if service_ext:
            order.append(service_ext)
    did_ext = settings.did_to_ext.get(str(did or "").strip())
    if did_ext and did_ext in settings.sales_order:
        order.append(did_ext)
    order.extend(settings.sales_order)

    seen: set[str] = set()
    uniq = [e for e in order if not (e in seen or seen.add(e))]
    return uniq, resolved


def _call_age_sec(row: CallEvent, now: float | None = None) -> float:
    """Возраст записи звонка в секундах. Naive datetime считаем UTC."""
    now = time.time() if now is None else now
    ts = row.created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return now - ts.timestamp()


def is_voicemail(direction: str, answered: bool, duration: int, disposition: str) -> bool:
    """Похоже ли соединение на голосовую почту, а не на разговор.

    Признак — соединение состоялось, но длилось меньше `call_short_talk_sec`:
    автоответчик снимает трубку сам, менеджер слышит машину и сразу кладёт.
    Живой разговор короче 15 секунд бывает, но в исходящем обзвоне это редкость,
    а цена ошибки обратная: voicemail, посчитанный разговором, закрывает лид."""
    if (disposition or "").upper().replace(" ", "") == "VOICEMAIL":
        return True
    if direction != "out" or not answered:
        return False
    return 0 < duration < settings.call_short_talk_sec


def _is_answered(row: CallEvent) -> bool:
    """Состоявшийся разговор. Лестницу недозвона не поднимает.

    Голосовая почта разговором не считается: иначе после автоответчика лестница
    обнулялась и менеджер мог набирать тот же номер весь день."""
    disp = (row.disposition or "").upper().replace(" ", "")
    if disp in {"NOANSWER", "BUSY", "FAILED", "CONGESTION", "CHANUNAVAIL",
                "CANCEL", "VOICEMAIL"}:
        return False
    duration = row.duration or 0
    if row.direction == "out" and 0 < duration < settings.call_short_talk_sec:
        return False
    if disp.startswith("ANSWER"):
        return True
    return duration > 0


def _blocked(mode: str, reason: str) -> tuple[str, str]:
    return ("deny" if mode == "block" else "soft"), reason


def lead_status_for_phone(client: Any, phone: str) -> int | None:
    """Открытая сделка по номеру: нужен этап Reactivation в стороже."""
    phone = normalize_phone(phone)
    if not phone:
        return None
    contact = _oldest_contact(client, phone)
    if not contact:
        return None
    lead = _open_lead(client, int(contact["id"]))
    return int(lead["status_id"]) if lead and lead.get("status_id") else None


def maybe_move_to_reactivation(ctx: Ctx, phone: str, lead_id: int | None) -> bool:
    """Третий недозвон за сутки → этап Reactivation. Иначе запись врёт."""
    if not lead_id or not settings.status_reactivation:
        return False
    try:
        lead = ctx.client.get_lead(int(lead_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("reactivation lookup failed lead=%s: %s", lead_id, exc)
        return False
    if not lead:
        return False
    if lead.get("pipeline_id") != settings.pipeline_id:
        return False
    status = lead.get("status_id")
    if status in (settings.status_won, settings.status_lost, settings.status_reactivation):
        return False
    now = time.time()
    rows = list(ctx.s.scalars(
        select(CallEvent)
        .where(CallEvent.phone == phone, CallEvent.direction == "out")
        .order_by(CallEvent.id.desc())
        .limit(50)
    ))
    no_answers = [r for r in rows
                  if _call_age_sec(r, now) < 24 * 3600 and not _is_answered(r)]
    if len(no_answers) < settings.dial_guard_max_per_day:
        return False
    ctx.client.update_lead(int(lead_id), {"status_id": settings.status_reactivation})
    log_decision(ctx, "call.reactivation", lead=lead_id, phone=phone,
                 no_answers=len(no_answers))
    return True


# ── сторож дозвона: чтобы номер не заработал спам-метку заново ──
def dial_guard(session: Any, phone: str,
               lead_status_id: int | None = None) -> tuple[str, str]:
    """Можно ли сейчас набирать этот номер. Возвращает вердикт и причину.

    Лестница 27.08 (схема Павла): после 1-го недозвона 15 минут, после 2-го
    3 часа, после 3-го сделка уходит в Reactivation и дальше один звонок
    в сутки. Успешный разговор лестницу недозвона не поднимает. После любого
    набора, в том числе дозвона, всё равно ждём `dial_guard_min_gap_min`.

    Вердикты: `ok`, `soft` (режим warn), `deny` (режим block).
    """
    mode = (settings.dial_guard_mode or "off").strip().lower()
    phone = normalize_phone(phone)
    if mode == "off" or not phone:
        return "ok", ""

    now = time.time()
    rows = list(session.scalars(
        select(CallEvent)
        .where(CallEvent.phone == phone, CallEvent.direction == "out")
        .order_by(CallEvent.id.desc())
        .limit(50)
    ))
    if not rows:
        if lead_status_id == settings.status_reactivation:
            return "ok", ""
        return "ok", ""

    today = [r for r in rows if _call_age_sec(r, now) < 24 * 3600]
    no_answers = [r for r in today if not _is_answered(r)]
    last = _call_age_sec(rows[0], now)

    if lead_status_id == settings.status_reactivation:
        window = settings.dial_guard_reactivation_hours * 3600
        if last < window:
            return _blocked(mode, "reactivation_24h")
        return "ok", ""

    if last < settings.dial_guard_hard_gap_sec:
        return _blocked(mode, f"redial_{int(last)}s")
    if len(today) >= settings.dial_guard_max_per_day:
        return _blocked(mode, f"day_limit_{len(today)}")
    if len(no_answers) >= settings.dial_guard_max_per_day:
        return _blocked(mode, f"day_limit_{len(no_answers)}")
    if len(no_answers) >= 2:
        last_miss = _call_age_sec(no_answers[0], now)
        if last_miss < settings.dial_guard_second_gap_min * 60:
            return _blocked(mode, "attempt2_3h")
    if last < settings.dial_guard_min_gap_min * 60:
        return _blocked(mode, "attempt1_15min")
    return "ok", ""


# ── подписанные ссылки на записи ──
def _rec_secret() -> str:
    return settings.rec_link_secret or settings.internal_api_key


def rec_sign(name: str) -> str:
    return hmac.new(_rec_secret().encode(), name.encode(), hashlib.sha256).hexdigest()[:24]


def rec_verify(name: str, sig: str) -> bool:
    if not _rec_secret() or not REC_NAME_RE.match(name or ""):
        return False
    return hmac.compare_digest(rec_sign(name), sig or "")


def rec_url(name: str) -> str:
    """Публичная ссылка на запись; пустая строка, если файла нет или имя странное."""
    name = (name or "").strip()
    if not name or not REC_NAME_RE.match(name) or not _rec_secret():
        return ""
    return f"{settings.public_base_url.rstrip('/')}/rec/{rec_sign(name)}/{name}"


# ── журнал звонков ──
def _int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _human_duration(sec: int) -> str:
    return f"{sec // 60} мин {sec % 60} с" if sec >= 60 else f"{sec} с"


def _oldest_contact(client: Any, phone: str) -> dict[str, Any] | None:
    contacts = find_contacts_by_phone(client, phone)
    if not contacts:
        return None
    return sorted(contacts, key=lambda c: c.get("created_at") or 0)[0]


def _open_lead(client: Any, contact_id: int) -> dict[str, Any] | None:
    from app.dedup_deals import load_leads

    opens = [l for l in load_leads(client, contact_id)
             if l.get("closed_at") in (None, 0) and l.get("status_id") not in CLOSED_STATUS_IDS]
    opens.sort(key=lambda l: l.get("created_at") or 0)
    return opens[0] if opens else None


def _write_call_note(ctx: Ctx, entity_type: str, entity_id: int, *, uniqueid: str,
                     phone: str, direction: str, duration: int, answered: bool,
                     recording: str, result: str, owner: int | None,
                     author: int | None = None) -> None:
    kommo_entity = "contacts" if entity_type == "contact" else "leads"
    link = rec_url(recording)
    params: dict[str, Any] = {
        "uniq": uniqueid,
        "duration": duration,
        "source": settings.telephony_source,
        "phone": phone,
        "call_status": CALL_ANSWERED if answered else CALL_MISSED,
    }
    if link:
        params["link"] = link
    if result:
        params["call_result"] = result
    note: dict[str, Any] = {
        "entity_id": entity_id,
        "note_type": "call_in" if direction == "in" else "call_out",
        "params": params,
    }
    if owner:
        note["responsible_user_id"] = int(owner)
    # Без created_by Kommo подписывает примечание пользователем интеграции — в
    # карточке выглядело так, будто клиенту звонил владелец аккаунта, а не менеджер.
    if author:
        note["created_by"] = int(author)
    try:
        ctx.client.add_note_raw(kommo_entity, note)
        log_decision(ctx, "call.note", entity=entity_type, id=entity_id,
                     uniq=uniqueid, answered=answered, duration=duration, link=bool(link),
                     author=author or 0)
        return
    except KommoError as exc:
        # Kommo может не принять примечание-звонок от «чужого» источника —
        # тогда пишем обычным текстом, чтобы звонок всё равно попал в карточку.
        log.warning("call note rejected (%s), fallback to common note", exc)
        log_decision(ctx, "call.note_fallback", entity=entity_type, id=entity_id,
                     error=str(exc)[:200])
    prefix = "Входящий" if direction == "in" else "Исходящий"
    text = (f"{prefix} звонок {phone}: не дозвонились" if not answered
            else f"{prefix} звонок {phone}, {_human_duration(duration)}")
    if result:
        text += f"\n{result}"
    if link:
        text += f"\nЗапись: {link}"
    ctx.client.add_note(kommo_entity, entity_id, text)


def _task_owner(ctx: Ctx, owner: int | None) -> int:
    """Кому задачу «перезвонить»: ответственному карточки, если он ведёт продажи.

    Задача на том, кто звонки не принимает, — задача, которой никто не видит.
    Звонок на линию продаж звонит у дежурного продавца, значит и задача его,
    на кого бы ни была записана карточка. Кто считается продавцом — `sells()`.
    """
    from app.assignment import sells

    if sells(ctx.client, owner):
        return int(owner)
    log.info("owner %s does not take sales calls, task goes to sales", owner)
    return settings.telephony_missed_owner_id


def _ensure_card(ctx: Ctx, phone: str, direction: str,
                 create: bool) -> tuple[int | None, int | None, int | None, int | None]:
    """Возвращает (contact_id, lead_id, responsible_user_id, pipeline_id).

    Если карточки нет и создавать можно — заводим через общий intake, чтобы
    сработали те же правила антидубля и распределения, что для заявок с сайта."""
    from app.intake import process_intake

    contact = _oldest_contact(ctx.client, phone)
    if contact is not None:
        contact_id = int(contact["id"])
        lead = _open_lead(ctx.client, contact_id)
        if lead:
            return (contact_id, int(lead["id"]), lead.get("responsible_user_id"),
                    lead.get("pipeline_id"))
        if not create:
            return contact_id, None, contact.get("responsible_user_id"), None
    elif not create:
        return None, None, None, None

    label = "Входящий звонок" if direction == "in" else "Исходящий звонок"
    res = process_intake(ctx, {
        "name": "", "phone": phone,
        "channel": settings.telephony_channel,
        "notes": f"{label} {phone}",
    })
    contact_id = res.get("contact_id")
    lead_id = res.get("lead_id")
    owner = res.get("owner")
    # свежая сделка по звонку всегда в воронке продаж, поэтому воронку не ищем
    if lead_id and not owner:
        lead = ctx.client.get_lead(int(lead_id))
        owner = (lead or {}).get("responsible_user_id")
    return contact_id, lead_id, owner, settings.pipeline_id if lead_id else None


def handle_call(ctx: Ctx, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Обработка события звонка из диалплана (вызывается worker'ом)."""
    uniqueid = str(payload.get("uniqueid") or "").strip() or f"gen-{int(time.time() * 1000)}"
    phone = normalize_phone(payload.get("phone"))
    direction = "out" if str(payload.get("direction") or "in").startswith("out") else "in"
    duration = max(_int(payload.get("duration")), _int(payload.get("billsec")),
                   _int(payload.get("answeredtime")))
    disposition = str(payload.get("disposition") or "").upper()
    connected = duration > 0 or disposition.startswith("ANSWER")
    # Разговор и соединение — разные вещи: автоответчик тоже «отвечает». Всё, что
    # ниже, считает разговором только `answered`, поэтому voicemail не закрывает
    # лид в отчётах и не сбрасывает лестницу недозвона (просьба Павла 24.08.2026).
    voicemail = is_voicemail(direction, connected, duration, disposition)
    answered = connected and not voicemail
    recording = str(payload.get("recording") or "").strip()
    missed_event = event_type == "call_missed"
    # добавочный менеджера: кто набрал номер (исходящий) или кто снял трубку
    ext = str(payload.get("ext") or "").strip()
    caller = settings.ext_to_user.get(ext)
    branch = str(payload.get("branch") or "").strip().lower()
    # «2» в меню: клиент шёл в обслуживание, к Полине на добавочный 102
    service = branch == "service"
    # мост на голосового агента Pleep: клиент не взял трубку — итога от Pleep не
    # будет, ветку «недозвон» закрывает хаб сам
    ai_bridge = branch == "ai"

    row = ctx.s.scalar(select(CallEvent).where(CallEvent.uniqueid == uniqueid))
    if row is None:
        row = CallEvent(uniqueid=uniqueid, phone=phone or None, direction=direction)
        ctx.s.add(row)
    row.phone = phone or row.phone
    row.did = str(payload.get("did") or "") or row.did
    row.ext = ext or row.ext
    row.duration = max(row.duration or 0, duration)
    row.disposition = disposition or row.disposition
    row.recording = recording or row.recording
    ctx.s.flush()

    if not settings.enable_telephony:
        log_decision(ctx, "call.disabled", uniq=uniqueid, phone=phone, type=event_type)
        return {"action": "telephony_disabled", "uniq": uniqueid}
    if not phone:
        log_decision(ctx, "call.no_phone", uniq=uniqueid, type=event_type)
        return {"action": "no_phone", "uniq": uniqueid}

    ai_result = None
    if ai_bridge and settings.enable_leadflow:
        from app.leadflow import on_ai_call_finished

        lead_hint = payload.get("lead") or payload.get("lead_id")
        ai_result = on_ai_call_finished(
            ctx, phone, answered, int(lead_hint) if str(lead_hint or "").isdigit() else None
        )

    need_note = not row.note_done
    need_task = missed_event and not row.task_done
    if not need_note and not need_task:
        return {"action": "already_done", "uniq": uniqueid}

    # карточку заводим только по входящему, который дошёл до людей либо состоялся:
    # сброс на меню и исходящие в неизвестный номер мусора в CRM не создают
    create = direction == "in" and (answered or missed_event)
    contact_id, lead_id, owner, pipeline_id = _ensure_card(ctx, phone, direction, create)
    if not contact_id:
        log_decision(ctx, "call.no_card", uniq=uniqueid, phone=phone,
                     answered=answered, type=event_type)
        return {"action": "no_card", "uniq": uniqueid}

    row.contact_id = contact_id
    row.lead_id = lead_id
    entity_type = "deal" if lead_id else "contact"
    entity_id = int(lead_id or contact_id)
    if not owner:
        owner = settings.telephony_missed_owner_id

    # Обслуживание ведёт Полина: и когда клиент сам выбрал «2» в меню, и когда он
    # уже в «Сборке» — там продажа закончена, звонит он по своему заказу. Иначе
    # задача уходила в продажи и клиента дёргал не тот человек (03.09.2026).
    service_call = service or pipeline_id == settings.assembly_pipeline_id
    task_owner = None
    if need_task:
        task_owner = (settings.telephony_service_owner_id if service_call
                      else _task_owner(ctx, owner))

    if need_note:
        result = ""
        if missed_event:
            result = ("Пропущенный: клиент выбрал обслуживание, Полина не ответила"
                      if service else "Пропущенный: никто не ответил")
        elif ai_bridge and not answered:
            result = "AI-звонок Pleep: клиент не взял трубку"
        elif ai_bridge:
            result = "AI-звонок Pleep"
        elif voicemail:
            result = f"Автоответчик, разговора не было ({duration} с)"
        elif not answered:
            result = "Клиент положил трубку до соединения"
        # Звонок в карточке принадлежит тому, кто его вёл: добавочный из диалплана
        # важнее ответственного по сделке (Александра звонила по клиенту Илоны).
        # Трубку не сняли — автор тот, кому перезванивать: без `created_by` Kommo
        # подписывает примечание пользователем интеграции, и в карточке весь
        # журнал выглядел как звонки одного человека (жалоба 02.09.2026).
        author = caller or task_owner
        _write_call_note(ctx, entity_type, entity_id, uniqueid=uniqueid, phone=phone,
                         direction=direction, duration=duration, answered=answered,
                         recording=recording, result=result,
                         owner=caller or owner, author=author)
        row.note_done = True

    moved = False
    if direction == "out" and not answered and lead_id:
        moved = maybe_move_to_reactivation(ctx, phone, int(lead_id))

    task_id = None
    if need_task and task_owner:
        text = (f"Перезвонить: обслуживание, пропущенный звонок с {phone}" if service_call
                else f"Перезвонить: пропущенный звонок с {phone}")
        task_id = create_task(ctx, entity_type, entity_id, text, task_owner)
        row.task_done = True
    ctx.s.flush()

    return {"action": "call_logged", "uniq": uniqueid, "contact": contact_id,
            "lead": lead_id, "task": task_id, "answered": answered, "owner": owner,
            "caller": caller, "ai": ai_result, "reactivation": moved}
