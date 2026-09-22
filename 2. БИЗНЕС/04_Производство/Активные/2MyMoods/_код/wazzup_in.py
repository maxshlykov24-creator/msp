"""Входящие WhatsApp и Telegram Wazzup. Слова Полины → Маркетинг 2MY.

Текст Instagram этот ключ не присылает: канал в amo отдельный.
"""

from __future__ import annotations

import re

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
# Успешно реализовано (142) не трогаем: повторное сообщение открывает новую заявку.
ALLOW = {
    lib.PIPELINE_SALES_NEW: {lib.ST["new"], lib.ST["in_work"], lib.ST["lost"]},
    lib.PIPELINE_SALES_OLD: {80719358, 80719298, lib.ST["lost"]},
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


def _phones(contact: dict) -> list[str]:
    out = []
    for field in contact.get("custom_fields_values") or []:
        code = (field.get("field_code") or "").upper()
        name = (field.get("field_name") or "").lower()
        if code != "PHONE" and "тел" not in name:
            continue
        for item in field.get("values") or []:
            out.append(_digits(str(item.get("value") or "")))
    return out


def _open_sales_lead(amo: lib.Amo, tail: str) -> dict | None:
    st, body = amo.req("GET", f"/api/v4/leads?query={tail}&limit=20")
    if not (200 <= st < 300) or not isinstance(body, dict):
        return None
    leads = (body.get("_embedded") or {}).get("leads") or []
    leads.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    for lead in leads:
        allowed = ALLOW.get(lead.get("pipeline_id"))
        if not allowed or lead.get("status_id") not in allowed:
            continue
        full_st, full = amo.req("GET", f"/api/v4/leads/{lead['id']}?with=contacts")
        if not (200 <= full_st < 300) or not isinstance(full, dict):
            continue
        for link in (full.get("_embedded") or {}).get("contacts") or []:
            cst, contact = amo.req("GET", f"/api/v4/contacts/{link.get('id')}")
            if not (200 <= cst < 300) or not isinstance(contact, dict):
                continue
            if any(tail in phone for phone in _phones(contact)):
                return full
    return None


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
        tail = _digits(str(msg.get("chatId") or ""))[-10:]
        if len(tail) < 10:
            print(f"  wazzup word {found} chat without phone", flush=True)
            continue
        lead = _open_sales_lead(amo, tail)
        if not lead:
            print(f"  wazzup word {found} no lead on new, in work, or lost", flush=True)
            continue
        st, _ = amo.req("PATCH", f"/api/v4/leads/{lead['id']}", {
            "pipeline_id": lib.PIPELINE_MKT_NEW,
            "status_id": lib.ST["mkt_talk"],
        })
        print(f"  wazzup {lead['id']} {found} -> marketing [{st}]", flush=True)
