from __future__ import annotations

import re

import phonenumbers
from phonenumbers import PhoneNumberFormat

DEFAULT_REGION = "RU"

# Кандидат на номер внутри свободного текста: начинается с + или цифры,
# дальше цифры/скобки/дефисы/пробелы/точки длиной от 8 символов.
_PHONE_CANDIDATE_RE = re.compile(r"[+\d][\d()\-.\s]{8,}\d")


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


def extract_phone_from_text(text: str) -> str | None:
    """Достать телефон из произвольного сообщения.

    Терпимо к формату: +7 / 8 / 7, скобки, пробелы, дефисы, точки, а также
    лишний текст вокруг («мой номер 8 (900) 123-45-67, спасибо»). Возвращает
    нормализованные цифры (как normalize_phone) либо None, если номера нет.
    """
    if not text or not text.strip():
        return None

    # 1) Парсер phonenumbers по свободному тексту — находит номер среди слов.
    try:
        for match in phonenumbers.PhoneNumberMatcher(text, DEFAULT_REGION):
            if phonenumbers.is_valid_number(match.number):
                e164 = phonenumbers.format_number(match.number, PhoneNumberFormat.E164)
                norm = normalize_phone(e164)
                if norm:
                    return norm
    except Exception:
        pass

    # 2) Запасной путь: вырезаем «телефоноподобные» куски и нормализуем каждый.
    for cand in _PHONE_CANDIDATE_RE.findall(text):
        norm = normalize_phone(cand)
        if norm:
            return norm

    # 3) Последняя попытка — весь текст целиком.
    return normalize_phone(text)


def normalize_tracking(tracking: str) -> str:
    """Сравнение треков: только буквы/цифры, нижний регистр."""
    return "".join(c.lower() for c in tracking.strip() if c.isalnum())

