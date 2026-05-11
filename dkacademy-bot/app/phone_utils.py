from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberFormat

DEFAULT_REGION = "RU"


def normalize_phone(phone_str: str) -> str | None:
    raw = phone_str.strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    digits = "".join(c for c in e164 if c.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") or digits.startswith("3"):
        return digits
    return digits


def normalize_tracking(tracking: str) -> str:
    """Сравнение треков: только буквы/цифры, нижний регистр."""
    return "".join(c.lower() for c in tracking.strip() if c.isalnum())

