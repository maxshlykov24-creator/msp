from __future__ import annotations

from typing import Optional

from app.talkme_parse import normalize_phone


def digits10(raw: object) -> Optional[str]:
    """Последние 10 цифр российского номера. Короче 10 — None."""
    return normalize_phone(raw)


def e164(raw: object) -> Optional[str]:
    d = digits10(raw)
    if not d:
        return None
    return f"+7{d}"


def display(raw: object) -> Optional[str]:
    """+7 931 000-00-00."""
    d = digits10(raw)
    if not d:
        return None
    return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:10]}"
