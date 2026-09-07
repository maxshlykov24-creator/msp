"""Разбор российского номера. Держать в синхроне с keris-server/app/booking_logic.py."""
from __future__ import annotations

import re

_PHONE_CANDIDATE_RE = re.compile(r"[+\d][\d()\-.\s]{8,}\d")


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    elif len(digits) == 10:
        pass
    elif len(digits) > 10:
        digits = digits[-10:]
    else:
        return str(raw or "")
    return "+7" + digits if len(digits) == 10 else str(raw or "")


def is_canonical_phone(value: str) -> bool:
    return bool(value) and value.startswith("+7") and len(value) == 12 and value[1:].isdigit()


def extract_phone_from_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for cand in _PHONE_CANDIDATE_RE.findall(raw):
        n = normalize_phone(cand)
        if is_canonical_phone(n):
            return n
    n = normalize_phone(raw)
    return n if is_canonical_phone(n) else ""
