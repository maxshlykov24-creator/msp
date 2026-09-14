"""Сделка в amo и цепочка алертов, когда клиент ждёт человека."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from typing import Any

from bot import avito_match, nudge, store
from bot.alerts import WAIT_CALL, WAIT_CHAT, AlertBot, format_alert, next_ping, now_msk
from bot.config import settings

log = logging.getLogger("crm")

if str(settings.root / "tools") not in sys.path:
    sys.path.insert(0, str(settings.root / "tools"))
import amo_client  # noqa: E402

bot: AlertBot | None = None


def set_bot(instance: AlertBot | None) -> None:
    global bot
    bot = instance


def digits_price(raw: str) -> int:
    n = re.sub(r"\D", "", raw or "")
    return int(n) if n else 0


def snapshot(chat_id: str | int, history: list[dict], reason: str) -> dict:
    doc = store.load_doc(chat_id)
    avito = doc.get("avito") or {}
    name = nudge.extract_name(history) or ""
    phone = nudge.extract_phone_from_history(history)
    card = None
    if avito.get("title"):
        card = avito_match.match_card(avito.get("title") or "", avito.get("price") or "")
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
        car = avito.get("title") or nudge.extract_car(history) or ""
    if not price:
        price = digits_price(avito.get("price") or "")
    ask = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            ask = (msg.get("content") or "").strip()
            break
    channel = "Авито" if str(chat_id).startswith("av:") else "Telegram"
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
        "url": avito.get("url") or "",
        "channel": channel,
        "ask": ask,
        "dialog": _dialog(history),
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
    return None


def _lead_name(snap: dict) -> str:
    car = (snap.get("car") or "авто").strip()
    who = (snap.get("name") or "").strip()
    if who:
        return "%s · %s" % (car, who)
    return "%s · %s" % (car, snap.get("channel") or "чат")


def ensure_lead(snap: dict, doc: dict) -> dict:
    crm = dict(doc.get("crm") or {})
    lead_id = crm.get("lead_id")
    if lead_id:
        try:
            lead = amo_client.get_lead(int(lead_id))
        except amo_client.AmoError as exc:
            log.warning("сделка %s не читается: %s", lead_id, exc)
            lead = None
        if lead:
            amo_client.add_note(int(lead_id), note_text(snap))
            crm["status_id"] = lead.get("status_id")
            crm["lead_url"] = amo_client.lead_url(int(lead_id))
            snap["lead_id"] = int(lead_id)
            snap["lead_url"] = crm["lead_url"]
            snap["created"] = False
            doc["crm"] = crm
            return snap

    contact_id = crm.get("contact_id")
    reused = False
    if snap.get("phone"):
        found = amo_client.find_contact(snap["phone"])
        if found:
            contact_id = found.get("id")
            open_leads = amo_client.open_leads_of(found)
            if open_leads:
                lead = open_leads[0]
                lead_id = lead.get("id")
                amo_client.add_note(int(lead_id), note_text(snap))
                reused = True
                crm.update(
                    {
                        "lead_id": int(lead_id),
                        "contact_id": int(contact_id or 0) or None,
                        "status_id": lead.get("status_id"),
                        "lead_url": amo_client.lead_url(int(lead_id)),
                        "created": False,
                    }
                )
                snap["lead_id"] = int(lead_id)
                snap["lead_url"] = crm["lead_url"]
                snap["created"] = False
                snap["nags"] = int(lead.get("status_id") or 0) == amo_client.STATUS_NEW
                doc["crm"] = crm
                return snap
        if not contact_id:
            contact_id = amo_client.create_contact(snap.get("name") or "", snap["phone"])

    lead = amo_client.create_lead(
        name=_lead_name(snap),
        contact_id=int(contact_id) if contact_id else None,
        price=int(snap.get("price") or 0),
        source_enum=_source_enum(snap),
        fields=_fields(snap),
    )
    lead_id = int(lead["id"])
    amo_client.add_note(lead_id, note_text(snap))
    crm.update(
        {
            "lead_id": lead_id,
            "contact_id": int(contact_id) if contact_id else None,
            "status_id": lead.get("status_id") or amo_client.STATUS_NEW,
            "lead_url": amo_client.lead_url(lead_id),
            "created": True,
        }
    )
    snap["lead_id"] = lead_id
    snap["lead_url"] = crm["lead_url"]
    snap["created"] = True
    snap["nags"] = True
    doc["crm"] = crm
    return snap


def _start_alert(doc: dict, snap: dict, nags: bool) -> None:
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
            "ask": (snap.get("ask") or "")[:200],
            "lead_url": snap.get("lead_url"),
        },
    }
    crm["alert"] = alert
    doc["crm"] = crm


async def _ping(alert: dict, minutes: int) -> None:
    if not bot:
        return
    text = format_alert(alert.get("snap") or {}, minutes)
    await bot.send(text)


async def capture(chat_id: str | int, history: list[dict], reason: str) -> dict:
    """Сделка + примечание + старт алертов. Не пишет клиенту."""
    doc = store.load_doc(chat_id)
    snap = snapshot(chat_id, history, reason)
    nags = True
    try:
        snap = ensure_lead(snap, doc)
        nags = bool(snap.get("nags", True))
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
        await capture(chat_id, history, "handoff")
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
    if bot:
        head = "💬 <b>Клиент пишет, ответа нет</b> | DIVO"
        body = format_alert(snap, 0)
        # меняем шапку, остальное то же
        rest = "\n".join(body.splitlines()[1:])
        await bot.send(head + rest)


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
    alert["picked"] = "chat"
    crm["alert"] = alert
    doc["crm"] = crm
    store.save_doc(chat_id, doc)
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


async def tick() -> None:
    if bot:
        await bot.listen()
    for chat_id in store.all_chat_ids():
        doc = store.load_doc(chat_id)
        alert = ((doc.get("crm") or {}).get("alert") or {})
        if not alert.get("active"):
            continue
        if lead_moved(chat_id):
            continue
        if not alert.get("nags", True):
            continue
        started = datetime.fromisoformat(alert["started_at"])
        due = next_ping(started, alert.get("pings") or [])
        if due is None:
            continue
        await _ping(alert, due)
        alert["pings"] = list(alert.get("pings") or []) + [due]
        doc["crm"]["alert"] = alert
        store.save_doc(chat_id, doc)
