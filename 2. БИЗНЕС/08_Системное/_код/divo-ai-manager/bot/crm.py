"""Сделка в amo и цепочка алертов, когда клиент ждёт человека."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from bot import avito_match, nudge, store
from bot.alerts import (
    WAIT_CALL,
    WAIT_CHAT,
    AlertBot,
    _copay,
    _extra_notes,
    _own_car,
    brief_from_history,
    compact_thread,
    format_alert,
    format_done,
    media_context,
    media_kind_of,
    next_ping,
    now_msk,
    pretty_phone,
    take_keyboard,
    with_media_brief,
)
from bot.config import settings

log = logging.getLogger("crm")

URGENT_REASONS = frozenset({"phone", "call", "complaint", "handoff", "aftersale"})
# Номер сам по себе не глушит чат: клиент часто пишет вопрос и телефон одной
# пачкой. Карточку менеджеру всё равно шлём. Молчим только когда нужен человек.
PAUSE_REASONS = frozenset({"call", "complaint", "handoff", "stuck", "llm", "aftersale"})

if str(settings.root / "tools") not in sys.path:
    sys.path.insert(0, str(settings.root / "tools"))
import amo_client  # noqa: E402

bot: AlertBot | None = None
_locks: dict[str, asyncio.Lock] = {}


def set_bot(instance: AlertBot | None) -> None:
    global bot
    bot = instance
    if instance is not None:
        instance.on_take = on_take


def phone_key(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits if len(digits) == 11 else ""


def ping_allowed(alert: dict) -> bool:
    if not alert.get("active"):
        return False
    if alert.get("picked"):
        return False
    if not alert.get("nags", True):
        return False
    return True


def _lock(name: str) -> asyncio.Lock:
    lock = _locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _locks[name] = lock
    return lock


@asynccontextmanager
async def _alert_lock(chat_id: str | int, phone: str = "") -> AsyncIterator[None]:
    keys = [f"c:{chat_id}"]
    key = phone_key(phone)
    if key:
        keys.append(f"p:{key}")
    async with AsyncExitStack() as stack:
        for name in sorted(set(keys)):
            await stack.enter_async_context(_lock(name))
        yield


def _alerts_for_phone(phone: str):
    key = phone_key(phone)
    if not key:
        return
    for cid in store.all_chat_ids():
        doc = store.load_doc(cid)
        alert = dict((doc.get("crm") or {}).get("alert") or {})
        snap = alert.get("snap") or {}
        if phone_key(str(snap.get("phone") or "")) != key:
            continue
        yield str(cid), doc, alert


def _adopt_open_card(new: dict, phone: str, skip: str) -> None:
    """Один номер — одна живая карточка в группе, даже если чатов два."""
    for cid, _doc, other in _alerts_for_phone(phone):
        if cid == str(skip):
            continue
        if not other.get("active"):
            continue
        tg = other.get("tg")
        if not tg:
            continue
        new["tg"] = dict(tg)
        new["token"] = other.get("token") or new.get("token")
        new["nags"] = False
        new["started_at"] = other.get("started_at") or new.get("started_at")
        new["pings"] = list(other.get("pings") or [])
        new["posts"] = _uniq_posts((new.get("posts") or []) + (other.get("posts") or []))
        return


def _uniq_posts(rows: list) -> list[dict]:
    seen: set[tuple[int, int]] = set()
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            chat_id = int(row.get("chat_id") or 0)
            message_id = int(row.get("message_id") or 0)
        except (TypeError, ValueError):
            continue
        if not chat_id or not message_id:
            continue
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({"chat_id": chat_id, "message_id": message_id})
    return out[-20:]


def _remember_post(alert: dict, chat_id: int | str | None, message_id: int | None) -> None:
    if not chat_id or not message_id:
        return
    try:
        ref = {"chat_id": int(chat_id), "message_id": int(message_id)}
    except (TypeError, ValueError):
        return
    alert["posts"] = _uniq_posts(list(alert.get("posts") or []) + [ref])


def _alert_open(alert: dict) -> bool:
    """Живая карточка: ещё не взяли. Медиа без пингов тоже живая."""
    return bool(alert.get("active") and not alert.get("picked"))


def _persist_alert(chat_id: str | int | None, alert: dict) -> None:
    """Пишем posts на диск сразу: иначе сбой оставит карточку, которой нет в состоянии."""
    if not chat_id:
        return
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    fresh = dict(crmd.get("alert") or {})
    fresh["posts"] = _uniq_posts(list(fresh.get("posts") or []) + list(alert.get("posts") or []))
    if _alert_open(fresh):
        if alert.get("tg"):
            fresh["tg"] = dict(alert["tg"])
        if alert.get("snap"):
            fresh["snap"] = dict(alert["snap"])
        if alert.get("token"):
            fresh["token"] = alert["token"]
        if "wait" in alert:
            fresh["wait"] = alert.get("wait")
        if "button_ok" in alert:
            fresh["button_ok"] = bool(alert.get("button_ok"))
        if "pings" in alert:
            fresh["pings"] = list(alert.get("pings") or [])
    crmd["alert"] = fresh
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
    alert["posts"] = list(fresh.get("posts") or [])
    if _alert_open(fresh) and fresh.get("tg"):
        alert["tg"] = dict(fresh["tg"])


async def _sweep_posts(alert: dict, keep: dict | None = None) -> None:
    """В группе остаётся одно сообщение: текущая карточка. Остальные снимаем."""
    keep = dict(keep or alert.get("tg") or {})
    try:
        keep_chat = int(keep.get("chat_id") or 0)
        keep_mid = int(keep.get("message_id") or 0)
    except (TypeError, ValueError):
        keep_chat = keep_mid = 0
    if keep_chat and keep_mid:
        _remember_post(alert, keep_chat, keep_mid)
    left: list[dict] = []
    for row in list(alert.get("posts") or []):
        try:
            chat_id = int(row.get("chat_id") or 0)
            message_id = int(row.get("message_id") or 0)
        except (TypeError, ValueError):
            continue
        if not chat_id or not message_id:
            continue
        if chat_id == keep_chat and message_id == keep_mid:
            left.append({"chat_id": chat_id, "message_id": message_id})
            continue
        gone = False
        if bot:
            gone = await bot.delete(chat_id, message_id)
        if not gone:
            left.append({"chat_id": chat_id, "message_id": message_id})
    alert["posts"] = _uniq_posts(left)


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
        vin = (card.get("VIN") or "").split()[0] if (card.get("VIN") or "").strip() else ""
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
    if reason == "media":
        wait = WAIT_CHAT
    elif reason == "aftersale":
        wait = WAIT_CHAT
    else:
        wait = WAIT_CALL if phone or reason in {"phone", "call"} else WAIT_CHAT
    client_vins = nudge.extract_vins(history)
    crm = dict(doc.get("crm") or {})
    existing_buyer = reason == "aftersale" or nudge.is_existing_buyer(ask)
    core = {
        "reason": reason,
        "wait": wait,
        "name": name,
        "phone": phone,
        "car": car,
        "vin": vin,
        "client_vins": client_vins,
        "url": listing.get("url") or "",
        "lead_url": crm.get("lead_url") or "",
        "lead_id": crm.get("lead_id") or "",
        "channel": channel,
        "existing_buyer": existing_buyer,
    }
    return {
        "chat_id": str(chat_id),
        **core,
        "brand": brand,
        "model": model,
        "year": year,
        "km": km,
        "price": price,
        "peer": listing.get("peer") or "",
        "ask": ask,
        "brief": brief_from_history(history, reason),
        "handover": handover_from_history(history, core),
        "thread": compact_thread(history),
    }


REASON_NOTE = {
    "phone": "клиент оставил номер",
    "call": "клиент просит позвонить",
    "stuck": "агент в тупике, нужен человек",
    "complaint": "жалоба или конфликт",
    "handoff": "эскалация к менеджеру",
    "llm": "бот не смог ответить",
    "media": "клиент просит фото или видео",
    "aftersale": "уже покупал у нас, сервис после сделки",
}


def _user_blob(history: list[dict] | None) -> str:
    parts = []
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if text:
            parts.append(text.replace("ё", "е"))
    return " ".join(parts)


def handover_from_history(history: list[dict] | None, snap: dict | None = None) -> str:
    """Примечание в amo: передача фактов, не стенограмма. Чат в сделке уже есть."""
    snap = dict(snap or {})
    hist = list(history or [])
    vins = [
        str(v).strip().upper()
        for v in (snap.get("client_vins") or nudge.extract_vins(hist))
        if str(v).strip()
    ]
    blob = _user_blob(hist)
    low = blob.lower()
    why = REASON_NOTE.get(snap.get("reason") or "", snap.get("reason") or "нужен человек")
    wait = snap.get("wait") or ""
    task = "позвонить" if wait == WAIT_CALL or snap.get("phone") else "ответить в чате"
    lines = ["Передача менеджеру. %s. Нужно %s." % (_cap_first(why), task), ""]

    who = (snap.get("name") or "").strip()
    phone = pretty_phone(snap.get("phone") or "")
    contact = ", ".join(x for x in [who, phone] if x)
    if contact:
        lines.append("Клиент: %s" % contact)
    if snap.get("channel"):
        lines.append("Канал: %s" % snap["channel"])

    our = (snap.get("car") or "").strip()
    raw_vin = str(snap.get("vin") or "").strip()
    our_vin = raw_vin.split()[0] if raw_vin else ""
    if our or our_vin:
        lines.append("")
        bit = "Интерес: %s" % (our or "машина не названа")
        if our_vin:
            bit += ", VIN %s" % our_vin
        lines.append(bit)
    if snap.get("url"):
        lines.append("Объявление: %s" % snap["url"])

    job: list[str] = []
    if any(k in low for k in ("обмен", "trade", "свою машин", "мой авто")):
        if vins:
            job.append("обмен, %d авто клиента" % len(vins))
        else:
            job.append("обмен")
        if "доплат" in low:
            pay = _copay(blob)
            job.append(pay or "с доплатой")
    if "дистанц" in low:
        job.append("оценка дистанционно")
    elif any(k in low for k in ("приехать", "осмотр", "когда можно", "во сколько")):
        job.append("хочет на осмотр")
    if "кредит" in low or "рассрочк" in low:
        job.append("кредит")
    if "лизинг" in low:
        job.append("лизинг")
    if job:
        lines.append("")
        lines.append("Задача: %s." % ", ".join(job))
    if vins:
        lines.append("VIN клиента:")
        for vin in vins:
            lines.append("- %s" % vin)

    details: list[str] = []
    if "комисси" in low or "выкуплен" in low:
        details.append("Спрашивал, на комиссии или выкуплен.")
    own = _own_car(blob)
    if own:
        details.append(_cap_first(own) + ".")
    for extra in _extra_notes(blob):
        details.append(_cap_first(extra) + ".")
    if details:
        lines.append("")
        lines.append("Из диалога:")
        for item in details:
            lines.append("- %s" % item)
    return "\n".join(lines).strip() + "\n"


def _cap_first(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    return clean[0].upper() + clean[1:]


def note_text(snap: dict) -> str:
    handover = (snap.get("handover") or "").strip()
    if handover:
        return handover
    return handover_from_history([], snap)


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


def _apply_phone_to_doc(snap: dict, doc: dict) -> None:
    """Пишет номер в карточку контакта amo. Сделку не создаёт."""
    phone = str(snap.get("phone") or "").strip()
    formatted = amo_client.amo_phone(phone)
    if not formatted:
        return
    crmd = dict(doc.get("crm") or {})
    lead_id = crmd.get("lead_id") or snap.get("lead_id")
    contact_id = crmd.get("contact_id")
    if not lead_id:
        return
    try:
        if not contact_id:
            lead = amo_client.get_lead(int(lead_id))
            contacts = (lead.get("_embedded") or {}).get("contacts") or []
            if contacts:
                contact_id = contacts[0].get("id")
        if not contact_id:
            contact_id = amo_client.create_contact(snap.get("name") or "", phone)
            amo_client.link_contact(int(lead_id), int(contact_id))
            log.info("сделка %s: создал контакт %s под номер", lead_id, contact_id)
        else:
            amo_client.set_contact_phone(int(contact_id), phone)
        crmd["contact_id"] = int(contact_id)
        crmd["phone"] = formatted
        doc["crm"] = crmd
        snap["phone"] = phone
        log.info("сделка %s: телефон %s на контакт %s", lead_id, formatted, contact_id)
    except amo_client.AmoError as exc:
        log.warning("сделка %s: телефон в amo не записался: %s", lead_id, exc)


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
    _apply_phone_to_doc(snap, doc)
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


def _start_alert(doc: dict, snap: dict, nags: bool, *, reuse_tg: bool = True) -> None:
    if not snap.get("phone") and snap.get("reason") not in {
        "llm",
        "media",
        "aftersale",
        "handoff",
        "complaint",
    }:
        return
    crm = dict(doc.get("crm") or {})
    alert = dict(crm.get("alert") or {})
    if alert.get("active") and alert.get("reason") == snap.get("reason"):
        inner = dict(alert.get("snap") or {})
        for key in (
            "phone",
            "name",
            "car",
            "channel",
            "lead_url",
            "lead_id",
            "url",
            "brief",
            "media_kind",
            "wait",
            "client_vins",
            "existing_buyer",
        ):
            val = snap.get(key)
            if val:
                inner[key] = val
        alert["snap"] = inner
        crm["alert"] = alert
        doc["crm"] = crm
        return
    now = now_msk()
    new = {
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
            "client_vins": snap.get("client_vins") or [],
            "channel": snap.get("channel"),
            "reason": snap.get("reason"),
            "wait": snap.get("wait"),
            "brief": snap.get("brief") or "",
            "lead_url": snap.get("lead_url"),
            "lead_id": snap.get("lead_id"),
            "media_kind": snap.get("media_kind") or "",
            "url": snap.get("url") or "",
            "existing_buyer": bool(snap.get("existing_buyer") or snap.get("reason") == "aftersale"),
        },
        "token": secrets.token_hex(4),
    }
    # Повод сменился (оставил номер → просит звонок) — это тот же клиент.
    # Держим прежнее сообщение и прежний токен кнопки, иначе в группе
    # появляется вторая карточка на одного человека.
    # После «Связались» tg не берём: иначе затрём закрытую карточку.
    if reuse_tg and alert.get("tg") and not alert.get("picked"):
        new["tg"] = alert["tg"]
        new["token"] = alert.get("token") or new["token"]
        new["posts"] = list(alert.get("posts") or [])
    skip = str(doc.get("chat_id") or snap.get("chat_id") or "")
    _adopt_open_card(new, str(snap.get("phone") or ""), skip)
    alert = new
    crm["alert"] = alert
    doc["crm"] = crm


def _ensure_token(alert: dict) -> str:
    token = str(alert.get("token") or "").strip()
    if not token:
        token = secrets.token_hex(4)
        alert["token"] = token
    return token


async def _publish(
    alert: dict, text: str, *, replace: bool = False, chat_id: str | int | None = None
) -> None:
    """Пинг шлём новым сообщением, чтобы пришёл пуш. Старое сразу снимаем.

    Id всех карточек пишем в posts до удаления: если снять не вышло или
    заявку взяли в эту секунду, следующий тик добьёт хвост.
    Живая карточка всегда с кнопкой Звоню / Беру.
    """
    if not bot:
        return
    wait = (alert.get("snap") or {}).get("wait") or alert.get("wait") or WAIT_CHAT
    token = _ensure_token(alert)
    markup = None if alert.get("picked") else take_keyboard(token, wait)
    tg = dict(alert.get("tg") or {})
    old_chat = tg.get("chat_id")
    old_mid = tg.get("message_id")
    if old_chat and old_mid:
        _remember_post(alert, old_chat, old_mid)

    async def _after_send(sent: tuple[int, int]) -> None:
        _remember_post(alert, sent[0], sent[1])
        alert["tg"] = {"chat_id": sent[0], "message_id": sent[1]}
        alert["button_ok"] = bool(markup)
        _persist_alert(chat_id, alert)
        keep = dict(alert["tg"])
        if chat_id:
            disk = ((store.load_doc(chat_id).get("crm") or {}).get("alert") or {})
            if disk.get("picked") and disk.get("tg"):
                keep = dict(disk["tg"])
                alert["tg"] = dict(keep)
        await _sweep_posts(alert, keep=keep)
        _persist_alert(chat_id, alert)

    if replace:
        sent = await bot.send(text, markup)
        if sent:
            await _after_send(sent)
            return
        if old_chat and old_mid:
            await bot.edit(int(old_chat), int(old_mid), text, markup)
            alert["button_ok"] = bool(markup)
            await _sweep_posts(alert, keep={"chat_id": int(old_chat), "message_id": int(old_mid)})
            _persist_alert(chat_id, alert)
        return
    if old_chat and old_mid:
        if await bot.edit(int(old_chat), int(old_mid), text, markup):
            alert["button_ok"] = bool(markup)
            await _sweep_posts(alert, keep={"chat_id": int(old_chat), "message_id": int(old_mid)})
            _persist_alert(chat_id, alert)
            return
    sent = await bot.send(text, markup)
    if sent:
        await _after_send(sent)


async def _ping(alert: dict, minutes: int, chat_id: str | int | None = None) -> None:
    await _publish(
        alert,
        format_alert(alert.get("snap") or {}, minutes),
        replace=minutes > 0,
        chat_id=chat_id,
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
        snap = snapshot(chat_id, history, reason)
        doc = store.load_doc(chat_id)
        _apply_phone_to_doc(snap, doc)
        store.save_doc(chat_id, doc)
        return reason
    await capture(chat_id, history, reason)
    log.info("чат %s: срочная передача %s, не ждём модель", chat_id, reason)
    return reason


async def ack_callback(channel, chat_id: str | int, texts: list[str], reason: str) -> None:
    """Коротко подтверждаем клиенту и отдаём менеджеру."""
    if channel is None:
        return
    from bot import human

    if reason == "aftersale":
        text = human.for_chat(nudge.AFTERSALE_ACK)
        await channel.send(chat_id, text)
        store.log_line(chat_id, "никита", text)
        history = store.load_history(chat_id)
        last = history[-1] if history else {}
        if not (last.get("role") == "assistant" and (last.get("content") or "") == text):
            history = history + [{"role": "assistant", "content": text}]
            store.save_history(chat_id, history)
        log.info("чат %s: сервис после покупки, дальше человек", chat_id)
        return
    if reason != "call":
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
        snap = snapshot(chat_id, history, reason)
        doc = store.load_doc(chat_id)
        _apply_phone_to_doc(snap, doc)
        store.save_doc(chat_id, doc)
        return snap
    snap = snapshot(chat_id, history, reason)
    phone = str(snap.get("phone") or "")
    async with _alert_lock(chat_id, phone):
        if already_alerting(chat_id, reason):
            return snapshot(chat_id, history, reason)
        doc = store.load_doc(chat_id)
        existing = dict((doc.get("crm") or {}).get("alert") or {})
        if existing.get("active") and existing.get("reason") == "media" and reason == "phone":
            await _notify_media_locked(
                chat_id,
                existing.get("media") or "фото",
                history,
                force=True,
            )
            return snapshot(chat_id, history, "media")
        nags = True
        try:
            snap = ensure_lead(snap, doc)
            nags = bool(snap.get("nags", True))
            if reason in {"call", "complaint", "handoff", "aftersale"}:
                nags = True
        except Exception:
            log.exception("amo по чату %s не записалась", chat_id)
        _start_alert(doc, snap, nags)
        store.save_doc(chat_id, doc)
        alert = (doc.get("crm") or {}).get("alert") or {}
        if not ping_allowed(alert):
            if alert.get("tg"):
                await _publish(
                    alert,
                    format_alert(alert.get("snap") or {}, 0),
                    replace=False,
                    chat_id=chat_id,
                )
                doc["crm"]["alert"] = alert
                store.save_doc(chat_id, doc)
            return snap
        if 0 not in set(int(x) for x in (alert.get("pings") or [])):
            due = next_ping(
                datetime.fromisoformat(alert["started_at"]),
                alert.get("pings") or [],
            )
            if due == 0:
                await _ping(alert, 0, chat_id)
                alert["pings"] = [0]
                doc["crm"]["alert"] = alert
                store.save_doc(chat_id, doc)
        return snap


async def notify_media(
    chat_id: str | int,
    kind: str,
    history: list[dict],
    *,
    force: bool = False,
) -> None:
    """Фото/видео: та же карточка, что номер и звонок. Без chat_id и без сырой реплики."""
    phone = str(snapshot(chat_id, history, "media").get("phone") or "")
    async with _alert_lock(chat_id, phone):
        await _notify_media_locked(chat_id, kind, history, force=force)


def _media_gap(inner: dict, snap: dict) -> bool:
    """Карточка устарела: появился номер или ссылка, которых в посте ещё нет."""
    if snap.get("phone") and not inner.get("phone"):
        return True
    if snap.get("lead_url") and not inner.get("lead_url"):
        return True
    if snap.get("url") and not inner.get("lead_url") and not inner.get("url"):
        return True
    return False


async def _notify_media_locked(
    chat_id: str | int,
    kind: str,
    history: list[dict],
    *,
    force: bool = False,
) -> None:
    label = media_kind_of(kind)
    snap = snapshot(chat_id, history, "media")
    snap["reason"] = "media"
    snap["wait"] = WAIT_CHAT
    snap["media_kind"] = label
    snap["brief"] = media_context(history, label)
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    if not snap.get("lead_url"):
        snap["lead_url"] = crmd.get("lead_url")
        snap["lead_id"] = crmd.get("lead_id") or snap.get("lead_id")
    listing = doc.get("avito") or doc.get("autoru") or {}
    if not snap.get("url"):
        snap["url"] = listing.get("url") or ""
    try:
        snap = ensure_lead(snap, doc)
    except Exception:
        log.exception("amo по чату %s для медиа не записалась", chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    inner = dict(alert.get("snap") or {})
    if not snap.get("lead_url") and inner.get("lead_url"):
        snap["lead_url"] = inner.get("lead_url")
        snap["lead_id"] = inner.get("lead_id") or snap.get("lead_id")
    if not snap.get("url") and inner.get("url"):
        snap["url"] = inner.get("url")
    if alert.get("active") and alert.get("tg"):
        gap = _media_gap(inner, snap)
        inner["phone"] = snap.get("phone") or inner.get("phone")
        inner["name"] = snap.get("name") or inner.get("name")
        inner["car"] = snap.get("car") or inner.get("car")
        inner["channel"] = snap.get("channel") or inner.get("channel")
        inner["lead_url"] = snap.get("lead_url") or inner.get("lead_url")
        inner["lead_id"] = snap.get("lead_id") or inner.get("lead_id")
        inner["url"] = snap.get("url") or inner.get("url")
        inner["brief"] = snap["brief"] or with_media_brief(inner.get("brief") or "", label)
        inner["media_kind"] = label
        inner["reason"] = "media"
        inner["wait"] = WAIT_CHAT
        alert["snap"] = inner
        alert["wait"] = WAIT_CHAT
        alert["media"] = label
        if alert.get("media_posted") == label and not force and not gap:
            crmd["alert"] = alert
            doc["crm"] = crmd
            store.save_doc(chat_id, doc)
            return
        ping = 0
        if not force:
            for item in reversed(list(alert.get("pings") or [])):
                try:
                    ping = int(item)
                    break
                except (TypeError, ValueError):
                    continue
        await _publish(
            alert,
            format_alert(inner, ping),
            replace=force,
            chat_id=chat_id,
        )
        alert["media_posted"] = label
        crmd["alert"] = alert
        doc["crm"] = crmd
        store.save_doc(chat_id, doc)
        return
    if alert.get("media") == label and alert.get("tg") and not force:
        return
    _start_alert(doc, snap, nags=False, reuse_tg=False)
    store.save_doc(chat_id, doc)
    alert = dict((doc.get("crm") or {}).get("alert") or {})
    if not alert:
        return
    alert["media"] = label
    await _publish(
        alert,
        format_alert(alert.get("snap") or snap, 0),
        replace=force,
        chat_id=chat_id,
    )
    alert["media_posted"] = label
    if 0 not in set(int(x) for x in (alert.get("pings") or [])):
        alert["pings"] = list(alert.get("pings") or []) + [0]
    doc.setdefault("crm", {})["alert"] = alert
    store.save_doc(chat_id, doc)


async def client_wrote_again(chat_id: str | int, text: str) -> None:
    """Клиент пишет, пока менеджер молчит: в канал клиента не отвечаем."""
    doc = store.load_doc(chat_id)
    history = list(doc.get("messages") or [])
    history.append({"role": "user", "content": text})
    doc["messages"] = history
    store.save_doc(chat_id, doc)
    phone_now = nudge.extract_phone_from_history(history)
    if phone_now:
        _apply_phone_to_doc(snapshot(chat_id, history, "phone"), doc)
        store.save_doc(chat_id, doc)
    crm = dict(doc.get("crm") or {})
    alert = dict(crm.get("alert") or {})
    if phone_now:
        inner = dict(alert.get("snap") or {})
        if inner and not inner.get("phone"):
            inner["phone"] = phone_now
            alert["snap"] = inner
            crm["alert"] = alert
            doc["crm"] = crm
            store.save_doc(chat_id, doc)
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
        # Дописываем в ту же карточку: вторая на одного клиента только путает.
        await _publish(alert, head + rest, replace=False, chat_id=chat_id)
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
        "Менеджер написал в чат, %s." % when,
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
        _remember_post(alert, chat_id, message_id)
        alert["tg"] = {"chat_id": int(chat_id), "message_id": int(message_id)}
        await bot.edit(int(chat_id), int(message_id), text, None, clear_markup=True)
        await _sweep_posts(alert, keep=alert["tg"])


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
        "Взял %s в %s." % (who, when),
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
            lines.insert(1, "Взял %s в %s." % (who, when))
            if chat_id and mid:
                await bot.edit(int(chat_id), int(mid), "\n".join(lines), None, clear_markup=True)
            await bot.answer_callback(cqid, "Тест. Сделку в amo не трогал")
        return
    cid = _chat_by_token(token)
    if not cid:
        if bot:
            await bot.answer_callback(cqid, "Этот алерт уже не активен")
        return
    taken = await claim(cid, who, when, message, user=user)
    if bot:
        if taken:
            await bot.answer_callback(cqid, "Взял")
        else:
            await bot.answer_callback(cqid, "Этот клиент уже взят, в amo ничего не менял")


def _silence_sibling(chat_id: str, who: str, when_iso: str, amo_user: int | None) -> None:
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    if not alert.get("active"):
        return
    alert["active"] = False
    alert["nags"] = False
    alert["picked"] = "button"
    alert["picked_by"] = who
    alert["picked_at"] = when_iso
    if amo_user:
        alert["picked_amo"] = int(amo_user)
    crmd["alert"] = alert
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)


async def claim(
    chat_id: str | int,
    who: str,
    when: str,
    fallback: dict | None = None,
    user: dict | None = None,
) -> bool:
    """True — взяли сейчас. False — кнопку уже нажимали, amo не трогаем.

    Двойной тап и повтор апдейта после перезапуска бота приходят как два
    callback-а: без этой проверки в сделку падало два одинаковых примечания.
    """
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    snap = dict(alert.get("snap") or {})
    phone = str(snap.get("phone") or "")
    async with _alert_lock(chat_id, phone):
        return await _claim_locked(chat_id, who, when, fallback, user)


async def _claim_locked(
    chat_id: str | int,
    who: str,
    when: str,
    fallback: dict | None = None,
    user: dict | None = None,
) -> bool:
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    snap = dict(alert.get("snap") or {})
    lead_id = snap.get("lead_id") or crmd.get("lead_id")
    if alert.get("picked") and not alert.get("active"):
        earlier = alert.get("picked_by") or who
        at = (alert.get("picked_at") or "")[11:16] or when
        log.info("чат %s: кнопку уже нажимал %s в %s, повтор игнорирую", chat_id, earlier, at)
        await _finish_message(alert, snap, earlier, at, fallback or {})
        return False
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
    # Метку ставим на диск до походов в amo: второй тап в эти секунды
    # должен увидеть, что клиент уже взят, и в примечания не писать.
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
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
    await _sweep_posts(alert, keep=alert.get("tg"))
    _persist_alert(chat_id, alert)
    phone = str(snap.get("phone") or "")
    if phone:
        for cid, _doc, _al in _alerts_for_phone(phone):
            if cid == str(chat_id):
                continue
            _silence_sibling(cid, who, str(alert.get("picked_at") or ""), amo_user)
    log.info("чат %s: взял %s amo=%s", chat_id, who, amo_user)
    return True


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
        "%s в amo, %s." % (label, when),
    )
    log.info("чат %s: %s, алерты снял", chat_id, kind)
    return True


async def _tick_one(chat_id: str) -> None:
    doc = store.load_doc(chat_id)
    alert = dict((doc.get("crm") or {}).get("alert") or {})
    phone = str((alert.get("snap") or {}).get("phone") or "")
    async with _alert_lock(chat_id, phone):
        doc = store.load_doc(chat_id)
        crmd = dict(doc.get("crm") or {})
        kind = crmd.pop("resend_media", None)
        if kind:
            doc["crm"] = crmd
            store.save_doc(chat_id, doc)
            await _notify_media_locked(
                chat_id,
                str(kind),
                list(doc.get("messages") or []),
                force=True,
            )
            return
        alert = dict(crmd.get("alert") or {})
        await _sweep_posts(alert, keep=alert.get("tg"))
        if _alert_open(alert) and alert.get("tg") and not alert.get("button_ok"):
            snap = dict(alert.get("snap") or {})
            await _publish(
                alert,
                format_alert(snap, 0),
                replace=False,
                chat_id=chat_id,
            )
            alert["button_ok"] = True
        doc.setdefault("crm", {})["alert"] = alert
        store.save_doc(chat_id, doc)
        if not ping_allowed(alert):
            return
        if lead_moved(chat_id):
            return
        if await close_if_contacted(chat_id, doc):
            return
        doc = store.load_doc(chat_id)
        alert = dict((doc.get("crm") or {}).get("alert") or {})
        if not ping_allowed(alert):
            await _sweep_posts(alert, keep=alert.get("tg"))
            doc.setdefault("crm", {})["alert"] = alert
            store.save_doc(chat_id, doc)
            return
        started = datetime.fromisoformat(alert["started_at"])
        due = next_ping(started, alert.get("pings") or [])
        if due is None:
            return
        snap = dict(alert.get("snap") or {})
        history = list(doc.get("messages") or [])
        if history:
            snap["brief"] = brief_from_history(history, alert.get("reason") or "")
            alert["snap"] = snap
        await _ping(alert, due, chat_id)
        doc = store.load_doc(chat_id)
        fresh = dict((doc.get("crm") or {}).get("alert") or {})
        if not ping_allowed(fresh):
            await _sweep_posts(alert, keep=fresh.get("tg") or alert.get("tg"))
            _persist_alert(chat_id, alert)
            return
        fresh["pings"] = list(fresh.get("pings") or []) + [due]
        if alert.get("tg"):
            fresh["tg"] = alert["tg"]
        if alert.get("snap"):
            fresh["snap"] = alert["snap"]
        if alert.get("posts"):
            fresh["posts"] = _uniq_posts(list(fresh.get("posts") or []) + list(alert.get("posts") or []))
        doc.setdefault("crm", {})["alert"] = fresh
        store.save_doc(chat_id, doc)


async def tick() -> None:
    if bot:
        await bot.listen(timeout=25)
    for chat_id in store.all_chat_ids():
        try:
            await _tick_one(str(chat_id))
        except Exception:
            log.exception("алерт чат %s", chat_id)
