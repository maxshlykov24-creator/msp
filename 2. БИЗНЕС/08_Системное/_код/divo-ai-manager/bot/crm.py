"""Сделка в amo и цепочка алертов, когда клиент ждёт человека."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import hashlib
import time
import threading
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
    working_wait,
    _esc,
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
    if str(skip).startswith(("av:", "ar:")):
        return
    for cid, _doc, other in _alerts_for_phone(phone):
        if cid == str(skip):
            continue
        if not other.get("active"):
            continue
        tg = other.get("tg")
        if not tg:
            continue
        # Один телефон может интересоваться несколькими объявлениями.
        # Общая карточка сохраняет каждое из них, а не затирает первое.
        interests = {}
        for snap in (other.get("snap") or {}, new.get("snap") or {}):
            for row in [snap] + list(snap.get("interests") or []):
                url = row.get("url") or row.get("lead_url")
                if url:
                    interests[url] = {"car": row.get("car") or "", "url": url}
        rows = list(interests.values())
        new.setdefault("snap", {})["interests"] = rows
        other.setdefault("snap", {})["interests"] = rows
        _doc.setdefault("crm", {})["alert"] = other
        store.save_doc(cid, _doc)
        new["tg"] = dict(tg)
        new["token"] = other.get("token") or new.get("token")
        new["nags"] = False
        new["started_at"] = other.get("started_at") or new.get("started_at")
        new["request_at"] = other.get("request_at") or other.get("started_at") or new.get("request_at")
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


def proven_phone(doc: dict) -> str:
    phone = nudge.extract_phone_from_history(doc.get("messages") or [])
    evidence = (doc.get("crm") or {}).get("phone_evidence") or {}
    if phone:
        return phone
    if evidence.get("source") == "client_message" and evidence.get("chat_id") == str(doc.get("chat_id")):
        return str(evidence.get("phone") or "")
    return ""


def snapshot(chat_id: str | int, history: list[dict], reason: str) -> dict:
    doc = store.load_doc(chat_id)
    listing = doc.get("avito") or doc.get("autoru") or {}
    name = nudge.extract_name(history) or listing.get("peer") or ""
    phone = nudge.extract_phone_from_history(history) or proven_phone(doc)
    card = None
    title = (listing.get("title") or "").strip()
    attached = bool(title) and "не привязано" not in title.lower() and title.lower() not in {
        "объявление",
        "чат",
    }
    if attached:
        card = avito_match.match_listing(listing)
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
    if not car and attached:
        car = title
    if not attached:
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
    if nudge.history_wants_write_here(history):
        wait = WAIT_CHAT
    elif reason == "media":
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
    task = "позвонить" if wait == WAIT_CALL or (not wait and snap.get("phone")) else "ответить в чате"
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
    recent = [m for m in hist if m.get("role") in {"user", "assistant"}][-8:]
    if recent:
        lines.extend(["", "Последние реплики (дословно):"])
        for message in recent:
            label = "Клиент" if message["role"] == "user" else "Бот"
            lines.append(label + ": " + str(message.get("content") or ""))
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
    if snap.get("url"):
        out[amo_client.FIELD_LISTING_URL] = snap["url"]
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


def _buyer_of(snap: dict) -> str:
    return str(snap.get("peer") or snap.get("name") or "")


def _forget_foreign_lead(snap: dict, doc: dict, crm: dict, lead_id: int) -> None:
    """Чат прилип к сделке другого человека. Карточку в amo не трогаем."""
    log.warning(
        "чат %s: сделка %s это другой клиент, не беру",
        snap.get("chat_id"),
        lead_id,
    )
    for key in ("lead_id", "note_lead_id", "contact_id", "lead_url", "verified_binding"):
        crm.pop(key, None)
    alert = dict(crm.get("alert") or {})
    inner = dict(alert.get("snap") or {})
    inner.pop("lead_id", None)
    inner.pop("lead_url", None)
    if alert:
        alert["snap"] = inner
        crm["alert"] = alert
    doc["crm"] = crm
    snap["lead_id"] = None
    snap["lead_url"] = None


def _marketplace_contact_id(lead: dict) -> int | None:
    ids = {int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts") or []}
    proven = lead.get("_provider_contact_id")
    if proven and int(proven) in ids:
        return int(proven)
    return next(iter(ids)) if len(ids) == 1 else None


def _verified_marketplace_lead(snap: dict, doc: dict) -> dict | None:
    channel = snap.get("channel") or ""
    if channel not in {"Авито", "Авто.ру"}:
        return None
    ar = doc.get("autoru") or {}
    binding = (doc.get("crm") or {}).get("verified_binding") or {}
    room = str(snap.get("chat_id") or doc.get("chat_id") or "")
    item = amo_client.listing_item_id(snap.get("url") or "")
    if (binding.get("version") == 1 and binding.get("chat_id") == room
            and binding.get("channel") == channel and binding.get("item_id") == item
            and binding.get("lead_id") and binding.get("contact_id")):
        lead = amo_client.get_lead(int(binding["lead_id"]))
        contacts = {int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts") or []}
        if (not lead.get("is_deleted") and lead.get("status_id") not in amo_client.CLOSED
                and int(binding["contact_id"]) in contacts
                and amo_client.listing_item_id(amo_client.lead_listing_url(lead)) == item):
            lead["_provider_contact_id"] = int(binding["contact_id"])
            return lead
    return amo_client.find_widget_lead(
        channel=channel, chat_id=str(snap.get("chat_id") or doc.get("chat_id") or ""),
        url=snap.get("url") or "", customer_id=ar.get("customer_id", ""),
    )


def _lead_is_other_buyer(snap: dict, lead: dict, doc: dict | None = None) -> bool:
    if snap.get("channel") not in {"Авито", "Авто.ру"}:
        return False
    verified = _verified_marketplace_lead(snap, doc or {})
    return not verified or int(verified["id"]) != int(lead["id"])


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
        if snap.get("channel") in {"Авито", "Авто.ру"}:
            verified = _verified_marketplace_lead(snap, doc)
            if not verified or int(verified["id"]) != int(lead_id):
                log.warning("сделка %s: запись номера заблокирована, нет точной привязки чата", lead_id)
                return
            contacts = (verified.get("_embedded") or {}).get("contacts") or []
            contact_id = _marketplace_contact_id(verified)
            if not contact_id:
                return
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
        log.info("сделка %s: телефон сохранён на контакт %s", lead_id, contact_id)
    except amo_client.AmoError as exc:
        log.warning("сделка %s: телефон в amo не записался: %s", lead_id, exc)


def _bind_lead(snap: dict, doc: dict, lead: dict, *, created: bool) -> dict:
    crm = dict(doc.get("crm") or {})
    lead_id = int(lead["id"])
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    if snap.get("channel") in {"Авито", "Авто.ру"}:
        verified = _verified_marketplace_lead(snap, doc)
        if not verified or int(verified["id"]) != lead_id:
            raise RuntimeError("CRM binding has no verified provider identity")
        proven_contact = _marketplace_contact_id(verified)
        if not proven_contact:
            raise RuntimeError("CRM contact identity is ambiguous")
        crm["verified_binding"] = {
            "version": 1, "chat_id": str(snap.get("chat_id") or doc.get("chat_id") or ""),
            "channel": snap["channel"], "item_id": amo_client.listing_item_id(snap.get("url") or ""),
            "lead_id": lead_id, "contact_id": proven_contact,
        }
    contact_id = crm.get("contact_id")
    if snap.get("channel") in {"Авито", "Авто.ру"}:
        contact_id = proven_contact
    elif contacts:
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
    verified = _verified_marketplace_lead(snap, doc)
    if not verified or int(verified["id"]) != lead_id:
        raise RuntimeError("Cannot adopt CRM lead without exact provider identity")
    refs = (widget.get("_embedded") or {}).get("contacts") or []
    proven_contact = _marketplace_contact_id(verified)
    if not proven_contact:
        raise RuntimeError("Cannot adopt marketplace lead without an unambiguous contact")
    doc.setdefault("crm", {})["verified_binding"] = {
        "version": 1, "chat_id": str(snap.get("chat_id") or doc.get("chat_id") or ""),
        "channel": snap["channel"], "item_id": amo_client.listing_item_id(snap.get("url") or ""),
        "lead_id": lead_id, "contact_id": proven_contact,
    }
    uid = widget.get("_unsorted_uid") or amo_client.find_unsorted_uid(lead_id)
    if uid:
        widget["_unsorted_uid"] = uid
    pipe = widget.get("pipeline_id")
    status = widget.get("status_id")
    alert = (doc.get("crm") or {}).get("alert") or {}
    picked = bool(alert.get("picked"))
    # A delayed CRM retry must not rewind a deal already handled by staff.
    if pipe != amo_client.PIPELINE_SALES or (not picked and status != amo_client.STATUS_NEW):
        widget = amo_client.move_lead_to_sales(
            lead_id,
            status_id=amo_client.STATUS_IN_WORK if picked else amo_client.STATUS_NEW,
            responsible_user_id=(alert.get("picked_amo") or widget.get("responsible_user_id")) if picked else amo_client.BOT_RESPONSIBLE,
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


def _owned_lead(snap: dict, doc: dict) -> dict:
    """Create an isolated lead/contact for one provider chat, with durable write intents."""
    cid = str(snap.get("chat_id") or "")
    phone = proven_phone(doc)
    if not phone_key(phone) or phone_key(phone) != phone_key(str(snap.get("phone") or "")):
        raise RuntimeError("No client phone evidence in this chat")
    marker = "DIVO_CHAT_" + hashlib.sha256(cid.encode()).hexdigest()[:32]
    state = doc.setdefault("crm", {})
    saved_creation = (store.load_doc(cid).get("crm") or {}).get("creation")
    if saved_creation:
        state["creation"] = saved_creation
    creation = state.setdefault("creation", {"marker": marker, "since": int(time.time())})
    if creation.get("marker") != marker:
        raise RuntimeError("Chat creation identity mismatch")

    def checkpoint():
        # This function also runs in a worker thread: never overwrite fresh messages.
        current = store.load_doc(cid)
        current.setdefault("crm", {})["creation"] = dict(creation)
        store.save_doc(cid, current)

    def entity(kind):
        key = "contact_id" if kind == "contacts" else "lead_id"
        if creation.get(key):
            return {"id": creation[key]}
        intent = kind + "_intent"
        if creation.get(intent):
            found = amo_client.owned_entity(kind, marker, creation["since"])
            if not found:
                raise RuntimeError("CRM write outcome unknown; reconcile before retry: " + kind)
        else:
            creation[intent] = True
            checkpoint()  # before POST; a lost response must never cause a second POST
            try:
                if kind == "contacts":
                    found = {"id": amo_client.create_contact(snap.get("name") or "", phone, marker=marker)}
                else:
                    found = amo_client.create_lead(name=_lead_name(snap), contact_id=creation["contact_id"],
                        price=int(snap.get("price") or 0), source_enum=_source_enum(snap),
                        fields=_fields(snap), marker=marker)
            except amo_client.AmoError as exc:
                # A rejected request cannot have created an entity. Network/5xx remain uncertain.
                if 400 <= exc.code < 500 and exc.code != 408:
                    creation.pop(intent, None)
                    checkpoint()
                raise
        creation[key] = int(found["id"])
        checkpoint()
        return found

    entity("contacts")
    entity("leads")
    lead = amo_client.get_lead(creation["lead_id"])
    contacts = {int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts") or []}
    if creation["contact_id"] not in contacts:
        raise RuntimeError("Created lead/contact link not confirmed")
    state["verified_binding"] = {
        "version": 1, "chat_id": cid, "channel": snap["channel"],
        "item_id": amo_client.listing_item_id(snap.get("url") or ""),
        "lead_id": lead["id"], "contact_id": creation["contact_id"], "source": "own_creation",
    }
    current = store.load_doc(cid)
    current.setdefault("crm", {}).update(creation=dict(creation), verified_binding=state["verified_binding"])
    store.save_doc(cid, current)
    result = _bind_lead(snap, doc, lead, created=True)
    alert = (doc.get("crm") or {}).get("alert") or {}
    if alert.get("picked") == "button":
        doc["crm"]["claim_pending"] = {"responsible": alert.get("picked_amo"),
            "who": alert.get("picked_by") or "Менеджер", "at": alert.get("picked_at") or now_msk().isoformat()}
    # Delivery is handled by the persistent notes queue, including after restart.
    queue_note(doc, "handoff", note_text(snap) + "\n\nЧат площадки: " + cid)
    queue_transcript(doc)
    return result


def queue_note(doc: dict, key: str, text: str) -> None:
    state = doc.setdefault("crm", {})
    if key in state.get("autoru_noted", []):
        return
    pending = state.setdefault("note_pending", [])
    if not any(r["id"] == key for r in pending):
        pending.append({"id": key, "text": text})


def queue_transcript(doc: dict) -> None:
    for row in doc.get("transcript") or []:
        # A live Auto.ru reply already has its own durable note event.
        state = doc.get("crm") or {}
        mid = str(row["id"])
        if mid in state.get("autoru_noted", []) or any(
                str(p.get("id")) == mid for p in state.get("note_pending", [])):
            continue
        queue_note(doc, "transcript:" + mid,
                   "%s | %s\n%s" % (row.get("at", ""), row["who"], row["text"]))


def transcript_actor(msg: dict, *, avito: bool, source: dict, own_ids: set) -> str:
    if avito:
        from bot.avito_loop import is_noise
        if is_noise(msg):
            return "Служебное сообщение"
        if msg.get("direction") == "in":
            return "Клиент"
        if msg.get("direction") == "out":
            return "Салон"
    else:
        from bot.autoru_loop import is_out
        if is_out(msg, str(source.get("me") or "")) or str(msg.get("id")) in own_ids:
            return "Салон"
        if source.get("customer_id") and str(msg.get("author") or "") == str(source["customer_id"]):
            return "Клиент"
    return "Автор не подтверждён"


async def refresh_transcript(chat_id: str, doc: dict) -> None:
    """Read actual provider history, including staff replies; never send messages."""
    from bot.avito import Avito
    from bot.autoru import Autoru
    from bot.avito_loop import content_text, created_of as av_at
    from bot.autoru_loop import created_of as ar_at, is_out
    av = chat_id.startswith("av:")
    api = Avito() if av else Autoru()
    rows = []
    try:
        if av:
            offset = 0
            seen = set()
            while True:
                page = await api.messages(chat_id[3:], limit=100, offset=offset)
                ids = {str(m.get("id")) for m in page}
                if page and not ids - seen:
                    raise RuntimeError("Provider history pagination did not advance")
                seen.update(ids)
                rows.extend(page)
                if len(page) < 100:
                    break
                offset += len(page)
        else:
            rows = await api.messages(chat_id[3:], count=1000)
    finally:
        await api.close()
    records = []
    source = doc.get("avito" if av else "autoru") or {}
    own_ids = {str(x) for x in (doc.get("crm") or {}).get("out_ids", [])}
    for m in rows:
        mid = str(m.get("id") or "")
        if not mid:
            continue
        if av:
            text = content_text(m)
            outbound = m.get("direction") == "out"
            at = av_at(m)
        else:
            payload = m.get("payload") or {}
            text = str(payload.get("value") or payload.get("text") or m.get("text") or "")
            outbound = is_out(m)
            at = ar_at(m)
        if not text:
            text = "[Вложение или служебное сообщение]"
        records.append({"id": mid, "at": datetime.fromtimestamp(at).astimezone().isoformat(),
                        "who": transcript_actor(m, avito=av, source=source, own_ids=own_ids), "text": text})
    doc["transcript"] = sorted(records, key=lambda r: r["at"])
    doc.setdefault("crm", {})["transcript_synced_at"] = time.time()


async def ensure_lead_async(snap: dict, doc: dict) -> dict:
    cid = str(snap.get("chat_id") or "")
    if phone_key(str(snap.get("phone") or "")) and cid.startswith(("av:", "ar:")):
        try:
            await refresh_transcript(cid, doc)
        except Exception:
            log.warning("чат %s: история площадки ожидает повторного чтения", cid)
    try:
        result = await asyncio.to_thread(ensure_lead, snap, doc)
    finally:
        current = store.load_doc(cid)
        target = current.setdefault("crm", {})
        changed = doc.get("crm") or {}
        for key in ("creation", "verified_binding", "lead_id", "contact_id", "status_id",
                    "lead_url", "created", "phone", "transcript_synced_at", "claim_pending"):
            if key in changed:
                target[key] = changed[key]
        for row in changed.get("note_pending") or []:
            queue_note(current, row["id"], row["text"])
        if "transcript" in doc:
            current["transcript"] = doc["transcript"]
        doc.clear()
        doc.update(current)
        store.save_doc(cid, doc)
    return result


_creation_locks: dict[str, Any] = {}


def ensure_lead(snap: dict, doc: dict) -> dict:
    lock = _creation_locks.setdefault(str(snap.get("chat_id") or ""), threading.RLock())
    with lock:
        return _ensure_lead_locked(snap, doc)


def _ensure_lead_locked(snap: dict, doc: dict) -> dict:
    # Без номера чат остаётся в текущем этапе. Нельзя ни создавать сделку,
    # ни принимать Неразобранное, включая медиа и отложенные передачи.
    if not phone_key(str(snap.get("phone") or "")):
        if snap.get("channel") in {"Авито", "Авто.ру"}:
            widget = amo_client.find_widget_lead(
                peer=snap.get("peer") or snap.get("name") or "",
                car=snap.get("car") or "", channel=snap["channel"],
                url=snap.get("url") or "", chat_id=str(snap.get("chat_id") or ""),
                customer_id=(doc.get("autoru") or {}).get("customer_id", ""),
                customer_names=(doc.get("autoru") or {}).get("customer_names", []),
            )
            if widget:
                snap["chat_url"] = amo_client.lead_url(int(widget["id"]))
        return snap
    crm = dict(doc.get("crm") or {})
    old_id = crm.get("lead_id") or crm.get("note_lead_id")
    channel = snap.get("channel") or ""

    if old_id:
        try:
            lead = amo_client.get_lead(int(old_id))
        except amo_client.AmoError as exc:
            log.warning("сделка %s не читается: %s", old_id, exc)
            lead = None
        if lead and _lead_is_other_buyer(snap, lead, doc):
            _forget_foreign_lead(snap, doc, crm, int(old_id))
            old_id = None
            lead = None
        if lead and lead.get("status_id") not in amo_client.CLOSED:
            if lead.get("pipeline_id") == amo_client.PIPELINE_TECH:
                return _adopt_widget(snap, doc, lead)
            if lead.get("pipeline_id") == amo_client.PIPELINE_SALES:
                queue_note(doc, "handoff", note_text(snap))
                return _bind_lead(snap, doc, lead, created=False)

    widget = None
    if channel in {"Авито", "Авто.ру"}:
        try:
            widget = amo_client.find_widget_lead(
                peer=snap.get("peer") or snap.get("name") or "",
                car=snap.get("car") or "",
                channel=channel,
                url=snap.get("url") or "",
                chat_id=str(snap.get("chat_id") or ""),
                customer_id=(doc.get("autoru") or {}).get("customer_id", ""),
                customer_names=(doc.get("autoru") or {}).get("customer_names", []),
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

    if channel in {"Авито", "Авто.ру"}:
        return _owned_lead(snap, doc)

    contact_id = crm.get("contact_id")
    if snap.get("phone"):
        found = amo_client.find_contact(snap["phone"])
        if found and amo_client.different_buyer(_buyer_of(snap), str(found.get("name") or "")):
            log.warning(
                "чат %s: контакт %s это %s, по номеру не беру",
                snap.get("chat_id"),
                found.get("id"),
                found.get("name") or "",
            )
            found = None
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
    if snap.get("reason") == "llm":
        return
    if not snap.get("phone") and snap.get("reason") not in {
        "media",
        "aftersale",
        "handoff",
        "complaint",
        "stuck",
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
            "chat_url",
            "ask",
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
        "request_at": crm.get("incoming_at") or now.isoformat(timespec="seconds"),
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
            "chat_url": snap.get("chat_url") or "",
            "chat_id": snap.get("chat_id") or "",
            "ask": snap.get("ask") or "",
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
        new["request_at"] = alert.get("request_at") or alert.get("started_at") or new["request_at"]
        new["tg"] = alert["tg"]
        new["token"] = alert.get("token") or new["token"]
        new["posts"] = list(alert.get("posts") or [])
    skip = str(doc.get("chat_id") or snap.get("chat_id") or "")
    _adopt_open_card(new, str(snap.get("phone") or ""), skip)
    alert = new
    crm["alert"] = alert
    doc["crm"] = crm


def dismiss_llm_alert(chat_id: str | int) -> None:
    """Бот сам ответил после сбоя модели — карточку «не смог» из группы снимаем."""
    doc = store.load_doc(chat_id)
    crmd = dict(doc.get("crm") or {})
    alert = dict(crmd.get("alert") or {})
    if alert.get("reason") != "llm":
        return
    if not alert.get("active"):
        return
    alert["active"] = False
    alert["nags"] = False
    crmd["alert"] = alert
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
    _queue_edit(alert, alert.get("snap") or {}, "Бот ответил сам.")
    log.info("чат %s: снял карточку «бот не смог ответить»", chat_id)


def _ensure_token(alert: dict) -> str:
    token = str(alert.get("token") or "").strip()
    if not token:
        token = secrets.token_hex(4)
        alert["token"] = token
    return token


async def _publish(
    alert: dict, text: str, *, replace: bool = False, chat_id: str | int | None = None
) -> bool:
    """Пинг шлём новым сообщением, чтобы пришёл пуш. Старое сразу снимаем.

    Id всех карточек пишем в posts до удаления: если снять не вышло или
    заявку взяли в эту секунду, следующий тик добьёт хвост.
    Живая карточка всегда с кнопкой Звоню / Беру.
    """
    if not bot:
        return False
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
            return True
        if old_chat and old_mid:
            if not await bot.edit(int(old_chat), int(old_mid), text, markup):
                return False
            alert["button_ok"] = bool(markup)
            await _sweep_posts(alert, keep={"chat_id": int(old_chat), "message_id": int(old_mid)})
            _persist_alert(chat_id, alert)
            return True
        return False
    if old_chat and old_mid:
        if await bot.edit(int(old_chat), int(old_mid), text, markup):
            alert["button_ok"] = bool(markup)
            await _sweep_posts(alert, keep={"chat_id": int(old_chat), "message_id": int(old_mid)})
            _persist_alert(chat_id, alert)
            return True
    sent = await bot.send(text, markup)
    if sent:
        await _after_send(sent)
        return True
    return False


async def _ping(alert: dict, minutes: int, chat_id: str | int | None = None) -> bool:
    return await _publish(
        alert,
        format_alert(alert.get("snap") or {}, minutes),
        replace=minutes > 0,
        chat_id=chat_id,
    )


def already_alerting(chat_id: str | int, reason: str) -> bool:
    alert = ((store.load_doc(chat_id).get("crm") or {}).get("alert") or {})
    pending = (store.load_doc(chat_id).get("crm") or {}).get("handoff_pending")
    return bool(alert.get("active") and alert.get("reason") == reason and not pending)


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
    if reason == "llm":
        # Сбой модели — техпроблема владельца, не карточка в группу менеджеров.
        # Ключ ожил — catchup сам ответит. Иначе «обмен без номера» висит как
        # «бот не смог ответить», хотя человеку там нечего брать.
        log.info("чат %s: сбой модели, группу не зовём", chat_id)
        return snapshot(chat_id, history, reason)
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
        # Сначала фиксируем намерение на диске. Сбой amo / рестарт не должен
        # оставлять только номер в истории и обещание модели.
        _start_alert(doc, snap, True)
        doc.setdefault("crm", {})["handoff_pending"] = {
            "reason": reason, "retry_at": now_msk().timestamp() + 60,
        }
        store.save_doc(chat_id, doc)
        nags = True
        lead_ready = False
        try:
            snap = await ensure_lead_async(snap, doc)
            lead_ready = bool((doc.get("crm") or {}).get("lead_id") and snap.get("lead_id"))
            nags = bool(snap.get("nags", True))
            if reason in {"call", "complaint", "handoff", "aftersale"}:
                nags = True
        except Exception:
            log.exception("amo по чату %s не записалась", chat_id)
        if lead_ready or not phone_key(phone):
            doc.setdefault("crm", {}).pop("handoff_pending", None)
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
                _persist_alert(chat_id, alert)
            return snap
        if 0 not in set(int(x) for x in (alert.get("pings") or [])):
            due = next_ping(
                datetime.fromisoformat(alert["started_at"]),
                alert.get("pings") or [],
            )
            if due == 0 and await _ping(alert, 0, chat_id):
                alert["pings"] = [0]
                _persist_alert(chat_id, alert)
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
        snap = await ensure_lead_async(snap, doc)
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
        inner["chat_url"] = snap.get("chat_url") or inner.get("chat_url")
        inner["ask"] = snap.get("ask") or inner.get("ask")
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
    last = history[-1] if history else {}
    if not (last.get("role") == "user" and
            (last.get("content") == text or str(last.get("content") or "").endswith("\n" + text))):
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
        head = "💬 <b>Новое сообщение от клиента</b> | DIVO"
        body = format_alert(snap, 0)
        rest = "\n".join(body.splitlines()[1:])
        # Дописываем в ту же карточку: вторая на одного клиента только путает.
        latest = "\n▪️ <b>Клиент написал:</b> %s" % _esc(" ".join(text.split())[:700])
        await _publish(alert, head + rest + latest, replace=False, chat_id=chat_id)
        crm["alert"] = alert
        doc["crm"] = crm
        store.save_doc(chat_id, doc)


def autoru_note_text(who: str, text: str) -> str:
    """Сохраняем реплику дословно, включая переносы строк."""
    return ("Клиент: " if who == "client" else "Бот: ") + str(text or "")


def _note_lead(chat_id: str, doc: dict) -> int | None:
    channel = "Авто.ру" if chat_id.startswith("ar:") else "Авито"
    src = doc.get("autoru" if channel == "Авто.ру" else "avito") or {}
    lead = _verified_marketplace_lead({"channel": channel, "chat_id": chat_id, "url": src.get("url", "")}, doc)
    return int(lead["id"]) if lead else None


def mirror_autoru_line(chat_id: str | int, who: str, text: str, msg_id: str = "") -> None:
    """Очередь ответов бота в примечания сделки. Только Авто.ру.

    В Авито переписка уже лежит в ленте виджета, примечание её дублирует.
    Вызывается только после успешной отправки. Не создаёт сделку и не меняет этап.
    CRM недоступна или сделка ещё не появилась — повторяем через tick.
    """
    key = str(chat_id)
    if not key.startswith("ar:") or who != "bot" or not text:
        return
    doc = store.load_doc(key)
    state = doc.setdefault("crm", {})
    if state.get("creation"):
        state["transcript_synced_at"] = 0
    dedupe = str(msg_id or "") or secrets.token_hex(16)
    if dedupe in state.get("autoru_noted", []):
        return
    pending = state.setdefault("note_pending", [])
    if not any(row["id"] == dedupe for row in pending):
        pending.append({"id": dedupe, "text": autoru_note_text(who, text)})
    store.save_doc(key, doc)


def note_text_equal(left: str, right: str) -> bool:
    # amoCRM HTML-escapes quotes in common notes on readback.
    import html
    return html.unescape(left) == html.unescape(right)


_note_locks: dict[str, asyncio.Lock] = {}
_note_tasks: dict[str, asyncio.Task] = {}


def schedule_notes(chat_id: str) -> None:
    """Try immediately after delivery; durable queue remains the retry source."""
    if chat_id in _note_tasks and not _note_tasks[chat_id].done():
        return
    task = asyncio.create_task(flush_notes(chat_id))
    _note_tasks[chat_id] = task
    def finished(done):
        if _note_tasks.get(chat_id) is done:
            _note_tasks.pop(chat_id, None)
        if not done.cancelled() and done.exception():
            log.warning("чат %s: запись примечания остаётся в очереди", chat_id)
    task.add_done_callback(finished)


async def flush_notes(chat_id: str) -> None:
    lock = _note_locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        await _flush_notes_locked(chat_id)


async def _flush_notes_locked(chat_id: str) -> None:
    import time
    doc = store.load_doc(chat_id)
    state = doc.get("crm") or {}
    pending = state.get("note_pending") or []
    if not pending or time.time() < state.get("note_retry_at", 0):
        return
    state["note_retry_at"] = time.time() + 60
    doc["crm"] = state
    store.save_doc(chat_id, doc)
    try:
        lead_id = await asyncio.to_thread(_note_lead, chat_id, doc)
        if not lead_id:
            current = store.load_doc(chat_id)
            current.setdefault("crm", {})["note_blocker"] = "provider_binding_missing"
            store.save_doc(chat_id, current)
            return
        current = store.load_doc(chat_id)
        current.setdefault("crm", {}).pop("note_blocker", None)
        store.save_doc(chat_id, current)
        # common notes не поддерживают external_id. До POST сохраняем границу
        # ID; при потере ответа сверяем точный текст среди более новых записей.
        notes = []
        page = 1
        while True:
            code, data = await asyncio.to_thread(amo_client.request,
                "/api/v4/leads/%s/notes" % lead_id, {"limit": "250", "page": str(page)})
            if code >= 400:
                return
            notes.extend(amo_client.items(data or {}, "notes"))
            if not (data or {}).get("_links", {}).get("next"):
                break
            page += 1
        for queued in list(pending):
            doc = store.load_doc(chat_id)
            state = doc.setdefault("crm", {})
            row = next((r for r in state.get("note_pending", []) if r["id"] == queued["id"]), None)
            if row is None:
                continue
            if "before_id" not in row or row.get("lead_id") != lead_id:
                row["before_id"] = max((int(n["id"]) for n in notes), default=0)
                row["lead_id"] = lead_id
                store.save_doc(chat_id, doc)
            delivered = any(int(n["id"]) > row["before_id"] and
                            n.get("note_type") == "common" and
                            note_text_equal((n.get("params") or {}).get("text", ""), row["text"]) for n in notes)
            if not delivered:
                if row.get("post_started"):
                    raise RuntimeError("CRM note outcome uncertain; reconcile before retry")
                row["post_started"] = time.time()
                store.save_doc(chat_id, doc)
                result = await asyncio.to_thread(amo_client.write, "/api/v4/leads/%s/notes" % lead_id, [{
                    "note_type": "common", "params": {"text": row["text"]},
                    "is_need_to_trigger_digital_pipeline": False,
                }], attempts=1)
                saved_notes = amo_client.items(result or {}, "notes")
                if not saved_notes:
                    raise RuntimeError("CRM note write returned no note id")
                for saved in saved_notes:
                    notes.append({"id": saved["id"], "note_type": "common", "params": {"text": row["text"]}})
            # Confirm the exact content by a fresh GET before acknowledging.
            matches = [n for n in notes if int(n["id"]) > row["before_id"]
                       and note_text_equal((n.get("params") or {}).get("text", ""), row["text"])]
            if not matches:
                raise RuntimeError("CRM note not confirmed")
            note_id = int(matches[-1]["id"])
            row["note_id"] = note_id
            store.save_doc(chat_id, doc)
            check_code, checked = await asyncio.to_thread(amo_client.request,
                "/api/v4/leads/%s/notes/%s" % (lead_id, note_id))
            if check_code != 200 or not note_text_equal((checked or {}).get("params", {}).get("text", ""), row["text"]):
                raise RuntimeError("CRM note readback failed")
            doc = store.load_doc(chat_id)
            state = doc.setdefault("crm", {})
            state["note_pending"] = [r for r in state.get("note_pending", []) if r["id"] != row["id"]]
            state.setdefault("autoru_noted", []).append(row["id"])
            state["autoru_noted"] = list(dict.fromkeys(state["autoru_noted"]))
            state["note_lead_id"] = lead_id
            # После await перечитали документ: новые входящие и очередь сохраняются.
            store.save_doc(chat_id, doc)
    except Exception:
        log.warning("чат %s: примечание ожидает повторной записи в amo", chat_id)


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
    sending = crm.get("sending") or {}
    from bot.avito_loop import outbound_text
    if (mid and sending.get("text") == outbound_text(msg)
            and abs(int(msg.get("created") or 0) - int(sending.get("at") or 0)) <= 120):
        remember_out(chat_id, msg)
        return False
    if mid and mid in ours:
        return False
    try:
        created = int(msg.get("created") or 0)
    except (TypeError, ValueError):
        created = 0
    out_at = int(crm.get("out_at") or 0)
    if not mid and created and out_at and abs(created - out_at) <= 45:
        return False
    # Исходящее сотрудника — часть диалога независимо от наличия алерта.
    # ID защищает историю от повторного опроса того же сообщения.
    foreign_ids = list(crm.get("foreign_out_ids") or [])
    if mid and mid in foreign_ids:
        return False
    from bot.avito_loop import outbound_text

    text = outbound_text(msg)
    if not text:
        return False
    if mid:
        crm["foreign_out_ids"] = (foreign_ids + [mid])[-80:]
    doc.setdefault("messages", []).append({
        "role": "assistant", "content": text, "author": "human", "message_id": mid,
    })
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
    store.pause(chat_id, "менеджер ответил в канале")
    store.log_line(chat_id, "менеджер", text)
    alert = dict(crm.get("alert") or {})
    if not alert.get("active"):
        return True
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
        snap = snapshot(chat_id, doc.get("messages") or [], "handoff")
        if _lead_is_other_buyer(snap, {"id": lead_id}, doc):
            return False
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
    elapsed = working_wait(
        alert.get("request_at") or alert.get("started_at") or "",
        alert.get("picked_at") or "",
    ) if alert.get("picked") == "button" else ""
    text = format_done(snap, line, elapsed, taken=alert.get("picked") == "button")
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
        "Взял <b>%s</b> в %s." % (_esc(who), _esc(when)),
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
            lines.insert(1, "Взял <b>%s</b> в %s." % (_esc(who), _esc(when)))
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
    if lead_id and snap.get("channel") in {"Авито", "Авто.ру"}:
        try:
            verified = _verified_marketplace_lead(snap, doc)
        except Exception:
            verified = None
        if not verified or int(verified["id"]) != int(lead_id):
            log.warning("кнопка: сделку %s не меняю, привязка чата не подтверждена", lead_id)
            lead_id = None
    if phone_key(str(snap.get("phone") or "")):
        crmd["claim_pending"] = {"responsible": amo_user, "who": who, "at": alert["picked_at"]}
    doc["crm"] = crmd
    store.save_doc(chat_id, doc)
    await flush_claim(str(chat_id))
    doc = store.load_doc(chat_id)
    crmd = doc.get("crm") or {}
    await _finish_message(alert, snap, who, when, fallback or {})
    await _sweep_posts(alert, keep=alert.get("tg"))
    _persist_alert(chat_id, alert)
    phone = str(snap.get("phone") or "")
    if phone and not str(chat_id).startswith(("av:", "ar:")):
        for cid, _doc, _al in _alerts_for_phone(phone):
            if cid == str(chat_id):
                continue
            _silence_sibling(cid, who, str(alert.get("picked_at") or ""), amo_user)
    log.info("чат %s: взял %s amo=%s", chat_id, who, amo_user)
    return True


async def flush_claim(chat_id: str) -> None:
    doc = store.load_doc(chat_id)
    state = doc.get("crm") or {}
    pending = state.get("claim_pending")
    if not pending or not state.get("lead_id"):
        return
    snap = dict((state.get("alert") or {}).get("snap") or {})
    snap.update(chat_id=chat_id, lead_id=state["lead_id"])
    try:
        lead = await asyncio.to_thread(_verified_marketplace_lead, snap, doc)
        if not lead or int(lead["id"]) != int(state["lead_id"]):
            return
        if lead.get("status_id") == amo_client.STATUS_NEW:
            await asyncio.to_thread(amo_client.set_lead_status, int(lead["id"]),
                amo_client.STATUS_IN_WORK, responsible_user_id=pending.get("responsible"))
        elif pending.get("responsible") and lead.get("responsible_user_id") != pending["responsible"]:
            # Do not rewind an advanced deal; assignment still must be recorded.
            await asyncio.to_thread(amo_client.write, "/api/v4/leads/%s" % lead["id"],
                {"responsible_user_id": pending["responsible"]}, method="PATCH")
        checked = await asyncio.to_thread(amo_client.get_lead, int(lead["id"]))
        if checked.get("status_id") == amo_client.STATUS_NEW:
            return
        if pending.get("responsible") and checked.get("responsible_user_id") != pending["responsible"]:
            return
        current = store.load_doc(chat_id)
        crm = current.setdefault("crm", {})
        if crm.get("claim_pending") != pending:
            return
        queue_note(current, "claim:" + pending["at"],
                   "Взял в работу: %s. Нажатие кнопки не подтверждает связь с клиентом." % pending["who"])
        crm.pop("claim_pending", None)
        crm["status_id"] = checked.get("status_id")
        crm["responsible_user_id"] = checked.get("responsible_user_id")
        store.save_doc(chat_id, current)
    except Exception:
        log.warning("чат %s: изменение CRM после кнопки ожидает повтора", chat_id)


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
        snap = snapshot(chat_id, doc.get("messages") or [], "handoff")
        if _lead_is_other_buyer(snap, {"id": lead_id}, doc):
            return False
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
    await flush_claim(chat_id)
    doc = store.load_doc(chat_id)
    crm_state = doc.get("crm") or {}
    if crm_state.get("creation", {}).get("lead_id") and time.time() - crm_state.get("transcript_synced_at", 0) >= 120:
        try:
            await refresh_transcript(chat_id, doc)
            current = store.load_doc(chat_id)
            current["transcript"] = doc["transcript"]
            current.setdefault("crm", {})["transcript_synced_at"] = time.time()
            queue_transcript(current)
            store.save_doc(chat_id, current)
            doc = current
        except Exception:
            log.warning("чат %s: переписка ожидает синхронизации", chat_id)
    state = doc.get("crm") or {}
    queued = state.get("handoff_pending") or {}
    alert = state.get("alert") or {}
    if queued and not phone_key(proven_phone(doc)):
        # Recover evidence from the provider; a truncated history is not a cancellation.
        try:
            await refresh_transcript(chat_id, doc)
            phone = nudge.extract_phone_from_history([
                {"role": "user", "content": r["text"]} for r in doc.get("transcript", []) if r["who"] == "Клиент"])
            if phone:
                state["phone_evidence"] = {"phone": phone, "chat_id": chat_id, "source": "client_message"}
            doc["crm"] = state
            store.save_doc(chat_id, doc)
        except Exception:
            log.warning("чат %s: телефон требует подтверждения по переписке", chat_id)
        if not phone_key(proven_phone(doc)):
            return
    if queued and now_msk().timestamp() >= queued.get("retry_at", 0):
        queued["retry_at"] = now_msk().timestamp() + 120
        state["handoff_pending"] = queued
        doc["crm"] = state
        store.save_doc(chat_id, doc)
        if alert.get("picked"):
            snap = snapshot(chat_id, list(doc.get("messages") or []), queued["reason"])
            try:
                snap = await ensure_lead_async(snap, doc)
                if (doc.get("crm") or {}).get("lead_id") and snap.get("lead_id"):
                    # Re-read after network calls, preserving newly arrived history.
                    current = store.load_doc(chat_id)
                    current.setdefault("crm", {}).update({k: v for k, v in doc["crm"].items()
                                                          if k not in {"alert", "handoff_pending", "note_pending"}})
                    current["crm"].pop("handoff_pending", None)
                    current["crm"].setdefault("alert", {}).setdefault("snap", {}).update(
                        lead_id=snap["lead_id"], lead_url=snap.get("lead_url", ""))
                    store.save_doc(chat_id, current)
            except Exception:
                log.warning("чат %s: CRM передача ожидает повторной записи", chat_id)
        else:
            await capture(chat_id, list(doc.get("messages") or []), queued["reason"])
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
        if not await _ping(alert, due, chat_id):
            return
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
    chat_ids = store.all_chat_ids()
    queued = []
    for cid in chat_ids:
        state = store.load_doc(cid).get("crm") or {}
        alert = state.get("alert") or {}
        if state.get("handoff_pending") or (ping_allowed(alert) and not alert.get("tg")):
            queued.append(cid)
    # Передачи имеют приоритет над архивом примечаний: медленная amo по
    # старому диалогу не должна задерживать сегодняшнюю заявку.
    for chat_id in queued + [cid for cid in chat_ids if cid not in queued]:
        try:
            await _tick_one(str(chat_id))
            await flush_notes(str(chat_id))
        except Exception:
            log.exception("алерт чат %s", chat_id)
