"""Входящие WhatsApp и Telegram Wazzup. Слова Полины → Маркетинг 2MY.

Текст Instagram этот ключ не присылает: канал в amo отдельный.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import lib

PHRASES = (
    "менеджер блогера",
    "сотрудничество",
    "реклама",
    "креатор",
    "бартер",
    "блогер",
    "статистика",
    "интеграция",
    "ugc",
)
# Провал двигаем только если у клиента нет ни одной открытой сделки.
EARLY = {
    lib.PIPELINE_SALES_NEW: {lib.ST["new"], lib.ST["in_work"]},
    lib.PIPELINE_SALES_OLD: {80719358, 80719298},
}


def _stem(phrase: str, token: str) -> bool:
    if len(phrase) < 6:
        return False
    stem = phrase[:-2] if phrase.endswith(("ия", "ая")) else phrase[:-1]
    return len(stem) >= 5 and token.startswith(stem)


def keyword(text: str) -> str | None:
    raw = (text or "").casefold().replace("ё", "е")
    if not raw:
        return None
    for phrase in PHRASES:
        if " " in phrase:
            if phrase in raw:
                return phrase
            continue
        for token in re.findall(r"[a-zа-я0-9]+", raw):
            if token == phrase or token.startswith(phrase) or _stem(phrase, token):
                return phrase
    if re.search(r"(?<![a-z])ai(?![a-z])", raw):
        return "ai"
    if re.search(r"(?<![а-я])ии(?![а-я])", raw):
        return "ии"
    return None


def _digits(value: str) -> str:
    return "".join(ch for ch in value or "" if ch.isdigit())


def _field_values(contact: dict) -> list[tuple[str, str, str]]:
    out = []
    for field in contact.get("custom_fields_values") or []:
        code = (field.get("field_code") or "").upper()
        name = (field.get("field_name") or "").lower().replace("_", "")
        for item in field.get("values") or []:
            out.append((code, name, str(item.get("value") or "")))
    return out


def _phones(contact: dict) -> list[str]:
    return [
        _digits(value)
        for code, name, value in _field_values(contact)
        if code == "PHONE" or "тел" in name
    ]


def _telegram_ids(contact: dict) -> list[str]:
    return [
        _digits(value)
        for _code, name, value in _field_values(contact)
        if "telegramid" in name
    ]


def _usernames(contact: dict) -> list[str]:
    return [
        value.lstrip("@").casefold()
        for _code, name, value in _field_values(contact)
        if "username" in name and value.strip()
    ]


def _hit(contact: dict, tail: str, tg_id: str, username: str) -> bool:
    if tail and any(tail in phone for phone in _phones(contact)):
        return True
    if tg_id and tg_id in _telegram_ids(contact):
        return True
    if username and username in _usernames(contact):
        return True
    return False


def _matches(amo: lib.Amo, lead: dict, tail: str, tg_id: str, username: str) -> dict | None:
    full_st, full = amo.req("GET", f"/api/v4/leads/{lead['id']}?with=contacts")
    if not (200 <= full_st < 300) or not isinstance(full, dict):
        return None
    for link in (full.get("_embedded") or {}).get("contacts") or []:
        cst, contact = amo.req("GET", f"/api/v4/contacts/{link.get('id')}")
        if not (200 <= cst < 300) or not isinstance(contact, dict):
            continue
        if _hit(contact, tail, tg_id, username):
            return full
    return None


def _open_sales_lead(amo: lib.Amo, tail: str, tg_id: str = "", username: str = "") -> dict | None:
    query = tail or tg_id or username
    if not query:
        return None
    st, body = amo.req("GET", f"/api/v4/leads?query={quote(query)}&limit=50")
    if not (200 <= st < 300) or not isinstance(body, dict):
        return None
    leads = (body.get("_embedded") or {}).get("leads") or []
    leads.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    matched = []
    for lead in leads:
        full = _matches(amo, lead, tail, tg_id, username)
        if full:
            matched.append(full)
    early = [
        lead for lead in matched
        if lead.get("status_id") in EARLY.get(lead.get("pipeline_id"), ())
    ]
    if early:
        return early[0]
    has_open = any(
        lead.get("status_id") not in (lib.ST["won"], lib.ST["lost"])
        for lead in matched
    )
    if has_open:
        return None
    lost = [
        lead for lead in matched
        if lead.get("status_id") == lib.ST["lost"]
        and lead.get("pipeline_id") in EARLY
    ]
    return lost[0] if lost else None


def handle_body(body: dict) -> None:
    amo = lib.Amo()
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("isEcho") or msg.get("status") != "inbound":
            continue
        if msg.get("type") not in (None, "text"):
            continue
        found = keyword(str(msg.get("text") or ""))
        if not found:
            continue
        contact = msg.get("contact") if isinstance(msg.get("contact"), dict) else {}
        phone_src = str(contact.get("phone") or msg.get("chatId") or "")
        tail = _digits(phone_src)
        tail = tail[-10:] if len(tail) >= 10 else ""
        chat_digits = _digits(str(msg.get("chatId") or ""))
        tg_id = chat_digits if 5 <= len(chat_digits) < 10 else ""
        username = str(contact.get("username") or "").lstrip("@").strip().casefold()
        if not tail and not tg_id and not username:
            print(f"  wazzup word {found} chat without phone", flush=True)
            continue
        lead = _open_sales_lead(amo, tail, tg_id, username)
        if not lead:
            print(f"  wazzup word {found} no early lead, lost kept because another deal is open", flush=True)
            continue
        st, _ = amo.req("PATCH", f"/api/v4/leads/{lead['id']}", {
            "pipeline_id": lib.PIPELINE_MKT_NEW,
            "status_id": lib.ST["mkt_talk"],
        })
        print(f"  wazzup {lead['id']} {found} -> marketing [{st}]", flush=True)
