"""Сделка в amo и цепочка алертов, когда клиент ждёт человека."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sys
from datetime import datetime
from typing import Any

from bot import avito_match, nudge, store
from bot.alerts import (
    WAIT_CALL,
    WAIT_CHAT,
    AlertBot,
    brief_from_history,
    compact_thread,
    format_alert,
    format_done,
    next_ping,
    now_msk,
    take_keyboard,
)
from bot.config import settings

log = logging.getLogger("crm")

URGENT_REASONS = frozenset({"phone", "call", "complaint", "handoff"})

if str(settings.root / "tools") not in sys.path:
    sys.path.insert(0, str(settings.root / "tools"))
import amo_client  # noqa: E402

bot: AlertBot | None = None


def set_bot(instance: AlertBot | None) -> None:
    global bot
    bot = instance
    if instance is not None:
        instance.on_take = on_take


def digits_price(raw: str) -> int:
    n = re.sub(r"\D", "", raw or "")
    return int(n) if n else 0


def snapshot(chat_id: str | int, history: list[dict], reason: str) -> dict:
    doc = store.load_doc(chat_id)
    listing = doc.get("avito") or doc.get("autoru") or {}
    name = nudge.extract_name(history) or listing.get("peer") or ""
    phone = nudge.extract_phone_from_history(history)
    card = None
    if listing.get("title"):
        card = avito_match.match_card(listing.get("title") or "", listing.get("price") or "")
    car = ""
    vin = brand = model = year = km = ""
    price = 0
    if card:
        car = card.get("title") or ""
        vin = (card.get("VIN") or "").split()[0]
        brand = card.get("Марка") or ""
        model = card.get("Модель") or ""
        year = re.sub(r"\D", "", card.get("Год") or card.get("title") or "")[:4]
        km = re.sub(r"\D", "", card.get("Пробег") or "")
        price = digits_price(card.get("Цена в объявлении") or "")
    if not car:
        car = listing.get("title") or ""
    if not car or "не привязано" in car.lower() or car == "объявление":
        car = nudge.extract_car(history) or car
    if not price:
        price = digits_price(listing.get("price") or "")
    ask = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            ask = (msg.get("content") or "").strip()
            break
    key = str(chat_id)
    if key.startswith("av:"):
        channel = "Авито"
    elif key.startswith("ar:"):
        channel = "Авто.ру"
    else:
        channel = "Telegram"
    wait = WAIT_CALL if phone or reason in {"phone", "call"} else WAIT_CHAT
    return {
        "chat_id": str(chat_id),
        "reason": reason,
        "wait": wait,
        "name": name,
        "phone": phone,
        "car": car,
        "vin": vin,
        "brand": brand,
        "model": model,
        "year": year,
        "km": km,
        "price": price,
        "url": listing.get("url") or "",
        "channel": channel,
        "peer": listing.get("peer") or "",
        "ask": ask,
        "dialog": _dialog(history),
        "brief": brief_from_history(history, reason),
        "thread": compact_thread(history),
    }


def _dialog(history: list[dict]) -> str:
    lines = []
    for msg in history[-40:]:
        who = "Клиент" if msg.get("role") == "user" else "Никита"
        text = re.sub(r"\s+", " ", (msg.get("content") or "").strip())
        if not text:
            continue
        lines.append("%s: %s" % (who, text))
    return "\n".join(lines)


def note_text(snap: dict) -> str:
    parts = [
        "AI-менеджер DIVO. Повод: %s."
        % {
            "phone": "клиент оставил номер",
            "call": "клиент просит позвонить",
            "stuck": "агент в тупике, нужен человек",
            "complaint": "жалоба или конфликт",
            "handoff": "эскалация к менеджеру",
            "llm": "бот не смог ответить",
        }.get(snap.get("reason") or "", snap.get("reason") or "ожидает менеджера"),
        "Канал: %s." % (snap.get("channel") or ""),
    ]
    if snap.get("name"):
        parts.append("Имя: %s." % snap["name"])
    if snap.get("phone"):
        parts.append("Телефон: %s." % snap["phone"])
    if snap.get("car"):
        parts.append("Авто: %s." % snap["car"])
    if snap.get("vin"):
        parts.append("VIN: %s." % snap["vin"])
    if snap.get("url"):
        parts.append("Объявление: %s." % snap["url"])
    if snap.get("ask"):
        parts.append("Последний запрос:\n%s" % snap["ask"])
    if snap.get("dialog"):
        parts.append("Переписка:\n%s" % snap["dialog"])
    return "\n".join(parts)


def _fields(snap: dict) -> dict[int, str]:
    out = {}
    if snap.get("vin"):
        out[amo_client.FIELD_VIN] = snap["vin"]
    if snap.get("brand"):
        out[amo_client.FIELD_BRAND] = snap["brand"]
    if snap.get("model"):
        out[amo_client.FIELD_MODEL] = snap["model"]
    if snap.get("year"):
        out[amo_client.FIELD_YEAR] = snap["year"]
    if snap.get("km"):
        out[amo_client.FIELD_KM] = snap["km"]
    return out


def _source_enum(snap: dict) -> int | None:
    if snap.get("channel") == "Авито":
        return amo_client.SOURCE_AVITO_CHAT
    if snap.get("channel") == "Авто.ру":
        return amo_client.SOURCE_AUTORU_CHAT
    return None


def _lead_name(snap: dict) -> str:
    car = (snap.get("car") or "авто").strip()
    who = (snap.get("name") or "").strip()
    if who:
        return "%s · %s" % (car, who)
    return "%s · %s" % (car, snap.get("channel") or "чат")


def _bind_lead(snap: dict, doc: dict, lead: dict, *, created: bool) -> dict:
    crm = dict(doc.get("crm") or {})
    lead_id = int(lead["id"])
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    contact_id = crm.get("contact_id")
    if contacts:
        contact_id = contacts[0].get("id") or contact_id
    crm.update(
        {
            "lead_id": lead_id,
            "contact_id": int(contact_id) if contact_id else None,
            "status_id": lead.get("status_id") or amo_client.STATUS_NEW,
            "lead_url": amo_client.lead_url(lead_id),
            "created": bool(created),
        }
    )
    snap["lead_id"] = lead_id
    snap["lead_url"] = crm["lead_url"]
    snap["created"] = bool(created)
    snap["nags"] = int(lead.get("status_id") or 0) == amo_client.STATUS_NEW
    doc["crm"] = crm
    return snap


def _adopt_widget(snap: dict, doc: dict, widget: dict) -> dict:
    lead_id = int(widget["id"])
    uid = widget.get("_unsorted_uid") or amo_client.find_unsorted_uid(lead_id)
    if uid:
        widget["_unsorted_uid"] = uid
    pipe = widget.get("pipeline_id")
    status = widget.get("status_id")
    if pipe != amo_client.PIPELINE_SALES or status != amo_client.STATUS_NEW:
        widget = amo_client.move_lead_to_sales(
            lead_id,
            price=int(snap.get("price") or 0),
            fields=_fields(snap),
            unsorted_uid=str(uid or ""),
        )
    contacts = (widget.get("_embedded") or {}).get("contacts") or []
    if not contacts:
        widget = amo_client.get_lead(lead_id)
        contacts = (widget.get("_embedded") or {}).get("contacts") or []
    contact_id = contacts[0].get("id") if contacts else None
    if contact_id and snap.get("phone"):
        amo_client.set_contact_phone(int(contact_id), snap["phone"])
    amo_client.add_note(lead_id, note_text(snap))
    log.info(
        "чат %s: взял сделку виджета %s в Продажи / Новая заявка",
        snap.get("chat_id"),
        lead_id,
    )
    return _bind_lead(snap, doc, widget, created=False)


def ensure_lead(snap: dict, doc: dict) -> dict:
    crm = dict(doc.get("crm") or {})
    old_id = crm.get("lead_id")
    channel = snap.get("channel") or ""

    if old_id:
        try:
            lead = amo_client.get_lead(int(old_id))
        except amo_client.AmoError as exc:
            log.warning("сделка %s не читается: %s", old_id, exc)
            lead = None
        if lead and lead.get("status_id") not in amo_client.CLOSED:
            if lead.get("pipeline_id") == amo_client.PIPELINE_TECH:
                return _adopt_widget(snap, doc, lead)
            if lead.get("pipeline_id") == amo_client.PIPELINE_SALES:
                amo_client.add_note(int(old_id), note_text(snap))
                return _bind_lead(snap, doc, lead, created=False)

    widget = None
    if channel in {"Авито", "Авто.ру"}:
        try:
            widget = amo_client.find_widget_lead(
                peer=snap.get("peer") or snap.get("name") or "",
                car=snap.get("car") or "",
                channel=channel,
            )
        except amo_client.AmoError as exc:
            log.warning("виджет amo по чату %s: %s", snap.get("chat_id"), exc)

    if widget:
        adopted = _adopt_widget(snap, doc, widget)
        new_id = adopted.get("lead_id")
        if old_id and int(old_id) != int(new_id or 0):
            try:
                amo_client.close_lead_spam(
                    int(old_id),
                    "Дубль AI-менеджера. Рабочая сделка: %s"
                    % amo_client.lead_url(int(new_id)),
                )
                log.info("чат %s: закрыл дубль %s", snap.get("chat_id"), old_id)
            except amo_client.AmoError as exc:
                log.warning("дубль %s не закрылся: %s", old_id, exc)
        return adopted

    contact_id = crm.get("contact_id")
    if snap.get("phone"):
        found = amo_client.find_contact(snap["phone"])
        if found:
            contact_id = found.get("id")
            open_leads = amo_client.open_leads_of(found)
            tech = [x for x in open_leads if x.get("pipeline_id") == amo_client.PIPELINE_TECH]
            sales = [x for x in open_leads if x.get("pipeline_id") == amo_client.PIPELINE_SALES]
            if tech:
                return _adopt_widget(snap, doc, tech[0])
            if sales:
                lead = sales[0]
                amo_client.add_note(int(lead["id"]), note_text(snap))
                return _bind_lead(snap, doc, lead, created=False)
        if not contact_id and channel == "Telegram":
            contact_id = amo_client.create_contact(snap.get("name") or "", snap["phone"])

    if channel in {"Авито", "Авто.ру"}:
        log.warning(
            "чат %s: сделки виджета в Техническом нет, новую в Продажах не создаю",
            snap.get("chat_id"),
        )
        return snap

    if snap.get("phone") and not contact_id:
        contact_id = amo_client.create_contact(snap.get("name") or "", snap["phone"])

    lead = amo_client.create_lead(
        name=_lead_name(snap),
        contact_id=int(contact_id) if contact_id else None,
        price=int(snap.get("price") or 0),
        source_enum=_source_enum(snap),
        fields=_fields(snap),
    )
    amo_client.add_note(int(lead["id"]), note_text(snap))
    return _bind_lead(snap, doc, lead, created=True)


def _start_alert(doc: dict, snap: dict, nags: bool) -> None:
    if not snap.get("phone") and snap.get("reason") != "llm":
        return
    crm = dict(doc.get("crm") or {})
    alert = dict(crm.get("alert") or {})
    if alert.get("active") and alert.get("reason") == snap.get("reason"):
        crm["alert"] = alert
        doc["crm"] = crm
        return
    now = now_msk()
    alert = {
        "active": True,
        "reason": snap.get("reason"),
        "wait": snap.get("wait"),
        "nags": bool(nags),
        "started_at": now.isoformat(timespec="seconds"),
        "pings": [],
        "snap": {
            "name": snap.get("name"),
            "phone": snap.get("phone"),
            "car": snap.get("car"),
            "channel": snap.get("channel"),
            "reason": snap.get("reason"),
            "wait": snap.get("wait"),
            "brief": snap.get("brief") or "",
            "lead_url": snap.get("lead_url"),
            "lead_id": snap.get("lead_id"),
        },
        "token": secrets.token_hex(4),
    }
    crm["alert"] = alert
    doc["crm"] = crm


async def _publish(alert: dict, text: str, *, replace: bool = False) -> None:
    """replace=True: новое сообщение (чтобы пинг ушёл в уведомления), старое снимаем."""
    if not bot:
        return
    wait = (alert.get("snap") or {}).get("wait") or alert.get("wait") or WAIT_CHAT
    token = alert.get("token") or ""
    markup = take_keyboard(token, wait) if token else None
    tg = dict(alert.get("tg") or {})
    old_chat = tg.get("chat_id")
    old_mid = tg.get("message_id")
    if replace:
        sent = await bot.send(text, markup)
        if sent:
            alert["tg"] = {"chat_id": sent[0], "message_id": sent[1]}
            if old_chat and old_mid and int(old_mid) != int(sent[1]):
                await bot.delete(int(old_chat), int(old_mid))
            return
        if old_chat and old_mid:
            await bot.edit(int(old_chat), int(old_mid), text, markup)
        return
    if old_chat and old_mid:
        if await bot.edit(int(old_chat), int(old_mid), text, markup):
            return
    sent = await bot.send(text, markup)
    if sent:
        alert["tg"] = {"chat_id": sent[0], "message_id": sent[1]}


async def _ping(alert: dict, minutes: int) -> None:
    await _publish(
        alert,
        format_alert(alert.get("snap") or {}, minutes),
        replace=minutes > 0,
    )


def already_alerting(chat_id: str | int, reason: str) -> bool:
    alert = ((store.load_doc(chat_id).get("crm") or {}).get("alert") or {})
    return bool(alert.get("active") and alert.get("reason") == reason)


async def capture_if_urgent(chat_id: str | int, texts: list[str]) -> str:
    """Номер, звонок, жалоба: карточка в группу сразу, не ждём LLM."""
    blob = "\n".join(str(t).strip() for t in (texts or []) if str(t).strip())
    if not blob:
        return ""
    history = store.load_history(chat_id)
    reason = nudge.urgent_reason(blob, history)
    if not reason:
        return ""
    last = history[-1] if history else {}
    if not (last.get("role") == "user" and (last.get("content") or "") == blob):
        history = history + [{"role": "user", "content": blob}]
        store.save_history(chat_id, history)
    if already_alerting(chat_id, reason):
        return reason
    await capture(chat_id, history, reason)
    log.info("чат %s: срочная передача %s, не ждём модель", chat_id, reason)
    return reason


async def ack_callback(channel, chat_id: str | int, texts: list[str], reason: str) -> None:
    """«Это вы звонили» — коротко подтверждаем и отдаём менеджеру, без сказки про дозвон."""
    if reason != "call" or channel is None:
        return
    blob = "\n".join(str(t).strip() for t in (texts or []) if str(t).strip())
    history = store.load_history(chat_id)
    if not (nudge.asks_about_call(blob) or nudge.is_caller_id_paste(blob, history)):
        return
    from bot import human

    text = human.for_chat(nudge.CALLBACK_ACK)
    await channel.send(chat_id, text)
    store.log_line(chat_id, "никита", text)
    history = store.load_history(chat_id)
    last = history[-1] if history else {}
    if not (last.get("role") == "assistant" and (last.get("content") or "") == text):
        history = history + [{"role": "assistant", "content": text}]
        store.save_history(chat_id, history)
    log.info("чат %s: подтвердил звонок салона, дальше человек", chat_id)


async def capture(chat_id: str | int, history: list[dict], reason: str) -> dict:
    """Сделка + примечание + старт алертов. Не пишет клиенту."""
    if already_alerting(chat_id, reason):
        return snapshot(chat_id, history, reason)
    doc = store.load_doc(chat_id)
    snap = snapshot(chat_id, history, reason)
    nags = True
    try:
        snap = ensure_lead(snap, doc)
        nags = bool(snap.get("nags", True))
        if reason in {"call", "complaint", "handoff"}:
            nags = True
    except Exception:
        log.exception("amo по чату %s не записалась", chat_id)
    _start_alert(doc, snap, nags)
    store.save_doc(chat_id, doc)
    alert = (doc.get("crm") or {}).get("alert") or {}
    if 0 not in set(int(x) for x in (alert.get("pings") or [])):
        due = next_ping(
            datetime.fromisoformat(alert["started_at"]),
            alert.get("pings") or [],
        )
        if due == 0:
            await _ping(alert, 0)
            alert["pings"] = [0]
            doc["crm"]["alert"] = alert
            store.save_doc(chat_id, doc)
    return snap


async def client_wrote_again(chat_id: str | int, text: str) -> None:
    """Клиент пишет, пока менеджер молчит: в канал клиента не отвечаем."""
    doc = store.load_doc(chat_id)
    history = list(doc.get("messages") or [])
    history.append({"role": "user", "content": text})
    doc["messages"] = history
    store.save_doc(chat_id, doc)
    crm = dict(doc.get("crm") or {})
    alert = dict(crm.get("alert") or {})
    if not alert.get("active"):
        prior = history[:-1]
        if nudge.is_caller_id_paste(text, prior):
            await capture(chat_id, history, "call")
        elif nudge.extract_phone(text) or nudge.history_has_phone(history):
            await capture(chat_id, history, "phone")
        return
    last = nudge.parse_iso(alert.get("echo_at") or "")
    if last and (now_msk() - last).total_seconds() < 180:
        return
    alert["echo_at"] = now_msk().isoformat(timespec="seconds")
    crm["alert"] = alert
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
    snap = dict(alert.get("snap") or {})
    snap["ask"] = text
    snap["brief"] = brief_from_history(history, alert.get("reason") or "handoff")
    alert["snap"] = snap
    crm["alert"] = alert
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
    if bot:
        head = "💬 <b>Клиент пишет, ответа нет</b> | DIVO"
        body = format_alert(snap, 0)
        rest = "\n".join(body.splitlines()[1:])
        await _publish(alert, head + rest, replace=True)
        crm["alert"] = alert
        doc["crm"] = crm
        store.save_doc(chat_id, doc)


def remember_out(chat_id: str | int, payload: Any) -> None:
    import time

    data = payload if isinstance(payload, dict) else {}
    mid = data.get("id") or (data.get("message") or {}).get("id")
    doc = store.load_doc(chat_id)
    crm = dict(doc.get("crm") or {})
    crm["out_at"] = int(data.get("created") or time.time())
    if mid is not None:
        ids = [str(x) for x in (crm.get("out_ids") or [])]
        sid = str(mid)
        if sid not in ids:
            ids.append(sid)
            crm["out_ids"] = ids[-80:]
    doc["crm"] = crm
    store.save_doc(chat_id, doc)


def on_foreign_out(chat_id: str | int, msg: dict) -> bool:
    """Исходящее в Авито, которое писал не бот: менеджер подхватил."""
    mid = str(msg.get("id") or "")
    doc = store.load_doc(chat_id)
    crm = dict(doc.get("crm") or {})
    ours = {str(x) for x in (crm.get("out_ids") or [])}
    if mid and mid in ours:
        return False
    try:
        created = int(msg.get("created") or 0)
    except (TypeError, ValueError):
        created = 0
    out_at = int(crm.get("out_at") or 0)
    if created and out_at and abs(created - out_at) <= 45:
        return False
    alert = dict(crm.get("alert") or {})
    if not alert.get("active"):
        return False
    alert["active"] = False
    alert["nags"] = False
    alert["picked"] = "chat"
    crm["alert"] = alert
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
    when = now_msk().strftime("%H:%M")
    _queue_edit(
        alert,
        alert.get("snap") or {},
        "Менеджер написал в чат, %s. Напоминать не буду." % when,
    )
    log.info("чат %s: менеджер ответил в канале", chat_id)
    return True


def lead_moved(chat_id: str | int) -> bool:
    doc = store.load_doc(chat_id)
    crm = dict(doc.get("crm") or {})
    lead_id = crm.get("lead_id")
    was = crm.get("status_id")
    alert = dict(crm.get("alert") or {})
    if not lead_id or not alert.get("active"):
        return False
    try:
        lead = amo_client.get_lead(int(lead_id))
    except amo_client.AmoError as exc:
        log.warning("проверка сделки %s: %s", lead_id, exc)
        return False
    now_status = lead.get("status_id")
    if now_status is None or now_status == was:
        return False
    alert["active"] = False
    alert["picked"] = "stage"
    crm["alert"] = alert
    crm["status_id"] = now_status
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
    log.info("чат %s: сделка %s ушла с этапа %s на %s", chat_id, lead_id, was, now_status)
    return True


def _chat_by_token(token: str) -> str | None:
    for cid in store.all_chat_ids():
        alert = ((store.load_doc(cid).get("crm") or {}).get("alert") or {})
        if alert.get("token") == token:
            return cid
    return None


def _started_ts(alert: dict) -> int:
    dt = nudge.parse_iso(alert.get("started_at") or "")
    if not dt:
        return 0
    return int(dt.timestamp())


async def _edit_alert(
    alert: dict, snap: dict, line: str, fallback: dict | None = None
) -> None:
    if not bot:
        return
    text = format_done(snap, line)
    extra = fallback or {}
    tg = alert.get("tg") or {}
    chat_id = tg.get("chat_id") or (extra.get("chat") or {}).get("id")
    message_id = tg.get("message_id") or extra.get("message_id")
    if chat_id and message_id:
        await bot.edit(int(chat_id), int(message_id), text, None)


def _queue_edit(alert: dict, snap: dict, line: str) -> None:
    if not bot:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_edit_alert(alert, snap, line))


async def _finish_message(alert: dict, snap: dict, who: str, when: str, fallback: dict) -> None:
    await _edit_alert(
        alert,
        snap,
        "Взял %s в %s. Напоминать не буду." % (who, when),
        fallback,
    )


async def on_take(cb: dict) -> None:
    data = str(cb.get("data") or "")
    cqid = str(cb.get("id") or "")
    user = cb.get("from") or {}
    who = user.get("first_name") or user.get("username") or "менеджер"
    when = now_msk().strftime("%H:%M")
    message = cb.get("message") or {}
    if not data.startswith("take:"):
        if bot:
            await bot.answer_callback(cqid)
        return
    token = data.split(":", 1)[1]
    if token == "demo":
        if bot:
            chat_id = (message.get("chat") or {}).get("id")
            mid = message.get("message_id")
            raw = message.get("text") or ""
            lines = raw.splitlines() or ["✅ В работе | DIVO"]
            lines[0] = "✅ <b>Связались</b> | DIVO"
            lines.insert(1, "Взял %s в %s. Напоминать не буду." % (who, when))
            if chat_id and mid:
                await bot.edit(int(chat_id), int(mid), "\n".join(lines), None)
            await bot.answer_callback(cqid, "Тест. Сделку в amo не трогал")
        return
    cid = _chat_by_token(token)
    if not cid:
        if bot:
            await bot.answer_callback(cqid, "Этот алерт уже не активен")
        return
    await claim(cid, who, when, message, user=user)
    if bot:
        await bot.answer_callback(cqid, "Взял, больше не напоминаю")


async def claim(
    chat_id: str | int,
    who: str,
    when: str,
    fallback: dict | None = None,
    user: dict | None = None,
) -> None:
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    snap = dict(alert.get("snap") or {})
    lead_id = snap.get("lead_id") or crmd.get("lead_id")
    person = user or {}
    amo_user = amo_client.resolve_responsible(
        tg_id=int(person.get("id") or 0),
        first=str(person.get("first_name") or ""),
        last=str(person.get("last_name") or ""),
        username=str(person.get("username") or ""),
    )
    amo_name = amo_client.user_name(amo_user) if amo_user else ""
    alert["active"] = False
    alert["nags"] = False
    alert["picked"] = "button"
    alert["picked_by"] = who
    alert["picked_at"] = now_msk().isoformat(timespec="seconds")
    if amo_user:
        alert["picked_amo"] = int(amo_user)
    crmd["alert"] = alert
    if amo_user is None:
        log.warning("кнопка: не нашёл amo-пользователя для %s tg=%s", who, person.get("id"))
    if lead_id:
        try:
            lead = amo_client.set_lead_status(
                int(lead_id),
                amo_client.STATUS_IN_WORK,
                responsible_user_id=amo_user,
            )
            crmd["status_id"] = lead.get("status_id") or amo_client.STATUS_IN_WORK
            if amo_user:
                crmd["responsible_user_id"] = int(amo_user)
            owner = amo_name or who
            amo_client.add_note(
                int(lead_id),
                "Менеджер нажал кнопку в Telegram (%s). "
                "Ответственный: %s. Сделку перевёл в Контакт установлен."
                % (who, owner),
            )
        except Exception:
            log.exception("этап сделки %s не сменился", lead_id)
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
    await _finish_message(alert, snap, who, when, fallback or {})
    log.info("чат %s: взял %s amo=%s", chat_id, who, amo_user)


async def close_if_contacted(chat_id: str | int, doc: dict) -> bool:
    """Связались: звонок в amo. Сообщение в чате гасит on_foreign_out."""
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    if not alert.get("active"):
        return False
    lead_id = (alert.get("snap") or {}).get("lead_id") or crmd.get("lead_id")
    since = _started_ts(alert)
    if not lead_id or not since:
        return False
    try:
        calls = amo_client.lead_calls_since(int(lead_id), since)
    except Exception:
        log.exception("звонки сделки %s", lead_id)
        return False
    if not calls:
        return False
    kind = str(calls[0].get("note_type") or "call_out")
    alert["active"] = False
    alert["nags"] = False
    alert["picked"] = "call"
    crmd["alert"] = alert
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
    label = "Входящий звонок" if kind == "call_in" else "Исходящий звонок"
    when = now_msk().strftime("%H:%M")
    await _edit_alert(
        alert,
        alert.get("snap") or {},
        "%s в amo, %s. Напоминать не буду." % (label, when),
    )
    log.info("чат %s: %s, алерты снял", chat_id, kind)
    return True


async def tick() -> None:
    if bot:
        await bot.listen(timeout=25)
    for chat_id in store.all_chat_ids():
        doc = store.load_doc(chat_id)
        alert = ((doc.get("crm") or {}).get("alert") or {})
        if not alert.get("active"):
            continue
        if lead_moved(chat_id):
            continue
        if await close_if_contacted(chat_id, doc):
            continue
        if not alert.get("nags", True):
            continue
        started = datetime.fromisoformat(alert["started_at"])
        due = next_ping(started, alert.get("pings") or [])
        if due is None:
            continue
        doc = store.load_doc(chat_id)
        if due > 0 and await close_if_contacted(chat_id, doc):
            continue
        alert = ((doc.get("crm") or {}).get("alert") or {})
        snap = dict(alert.get("snap") or {})
        history = list(doc.get("messages") or [])
        if history:
            snap["brief"] = brief_from_history(history, alert.get("reason") or "")
            alert["snap"] = snap
        await _ping(alert, due)
        alert["pings"] = list(alert.get("pings") or []) + [due]
        doc["crm"]["alert"] = alert
        store.save_doc(chat_id, doc)
