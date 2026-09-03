"""Идентификация клиента: нормализация телефона E.164 + вторичные ключи.

Ключ клиента — нормализованный телефон. Если телефона нет — вторичные id
(Teletype ID, ttad_id, Instagram username/chat id). По имени/нику авто не сливаем.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.config import settings
from app.kommo.client import KommoClient


# короче — не номер, а мусор в поле (код страны, «000», внутренний добавочный)
MIN_PHONE_DIGITS = 8
# без «+» страну не угадать, поэтому за международный считаем только длину,
# которая заведомо больше US-формата (11 цифр с ведущей 1 разобраны выше)
MIN_INTL_DIGITS = 11
MAX_PHONE_DIGITS = 15  # предел E.164


def normalize_phone(raw: str | None) -> str:
    """US-ориентированная нормализация к E.164 (+1XXXXXXXXXX).

    Возвращает "" если распознать не удалось (тогда работаем по вторичным ключам).
    """
    if not raw:
        return ""
    p = str(raw).strip()
    if p.startswith("p:"):
        p = p[2:]
    had_plus = p.startswith("+")
    digits = re.sub(r"\D", "", p)
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    # «+1», «12345» и прочие огрызки (у авто-контактов Wazzup поле бывает заполнено
    # одним кодом страны) — не номер: иначе все такие карточки склеятся в один «дубль».
    if had_plus and len(digits) >= MIN_PHONE_DIGITS:
        return "+" + digits
    # международный без «+»: так приходит CallerID из Telnyx (79689306832) — раньше
    # такой звонок терялся с call.no_phone и не попадал в Kommo
    if MIN_INTL_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
        return "+" + digits
    # неизвестный формат — не выдумываем страну
    return ""


# Имя одного и того же клиента приходит из разных каналов то кириллицей, то
# латиницей («Евгений Коека» из Facebook и «Evgeniy Koeka» из Tilda). Сравнение
# строк «как есть» считало их разными людьми и блокировало склейку дублей.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "e", "ґ": "g",
}
# длина, ниже которой вложенность токенов ничего не доказывает: «An» лежит в
# половине имён базы
_MIN_TOKEN_FOR_CONTAINS = 4


def normalize_name(name: str | None) -> str:
    """Имя к сопоставимому виду: латиница, нижний регистр, только буквы и пробелы."""
    s = (name or "").strip().lower()
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return " ".join(s.split())


def name_tokens(name: str | None) -> list[str]:
    return normalize_name(name).split()


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if (min(len(a), len(b)) >= _MIN_TOKEN_FOR_CONTAINS
            and (a in b or b in a)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= settings.name_similarity_ratio


def same_person_name(a: str | None, b: str | None) -> bool:
    """Похоже ли, что это имена одного человека.

    Терпит транслитерацию, разный порядок слов, отсутствие фамилии и опечатки
    («Eugene Smirnov» / «Eugene Smirnoff»). Пустое имя ни о чём не говорит —
    считаем совпадением, иначе безымянные авто-карточки Wazzup никогда не склеятся.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb or na == nb:
        return True
    if SequenceMatcher(None, na.replace(" ", ""), nb.replace(" ", "")).ratio() \
            >= settings.name_similarity_ratio:
        return True
    ta, tb = name_tokens(a), name_tokens(b)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(any(_tokens_match(x, y) for y in long_) for x in short)


def _cf_values(entity: dict[str, Any], field_id: int) -> list[str]:
    out: list[str] = []
    for cf in entity.get("custom_fields_values") or []:
        if cf.get("field_id") != field_id:
            continue
        for v in cf.get("values") or []:
            val = v.get("value")
            if val is not None and str(val).strip():
                out.append(str(val).strip())
    return out


def contact_phones(contact: dict[str, Any]) -> list[str]:
    return [normalize_phone(v) for v in _cf_values(contact, settings.field_phone) if normalize_phone(v)]


# Поля-идентификаторы: у контактов — id мессенджеров Wazzup (телефона может не
# быть), у сделок — Teletype/реклама. Порядок = приоритет поиска.
SECONDARY_FIELDS: dict[str, int] = {
    "wz_telegram_id": settings.field_wz_telegram_id,
    "wz_whatsapp_lid": settings.field_wz_whatsapp_lid,
    "wz_telegram_username": settings.field_wz_telegram_username,
    "wz_whatsapp_username": settings.field_wz_whatsapp_username,
    "teletype_id": settings.field_teletype_id,
    "ttad_id": settings.field_ttad_id,
}


def secondary_keys(entity: dict[str, Any]) -> dict[str, str]:
    """Вторичные идентификаторы из кастомных полей (мессенджеры, Teletype, реклама)."""
    keys: dict[str, str] = {}
    for name, fid in SECONDARY_FIELDS.items():
        vals = _cf_values(entity, fid)
        if vals:
            keys[name] = vals[0]
    return keys


def phone_query_variants(phone: str) -> list[str]:
    """Форматы запроса для поиска Kommo.

    В базе один и тот же номер лежит и как `7473361387`, и как `17473361387`,
    и как `+18188252213` (проверено на боевом аккаунте 2026-07-24). Поиск Kommo
    матчит подстроку цифр, поэтому национальные 10 цифр находят все варианты, а
    запрос `+1…` — только записи со страной. Национальный идёт первым.
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return []
    out = []
    if len(digits) == 11 and digits.startswith("1"):
        out.append(digits[1:])
    out += [digits, phone]
    seen: set[str] = set()
    return [q for q in out if q and not (q in seen or seen.add(q))]


def find_contacts_by_phone(client: KommoClient, phone: str) -> list[dict[str, Any]]:
    """Точный поиск контактов по телефону: query сужает выборку, затем сверяем
    нормализованное значение поля (Kommo query нечёткий)."""
    if not phone:
        return []
    found: dict[int, dict[str, Any]] = {}
    for raw_q in phone_query_variants(phone):
        for c in client.search_contacts(raw_q, limit=20):
            if phone in contact_phones(c):
                found[c["id"]] = c
        if found:
            break  # первый вариант самый широкий; остальные — запасные
    return list(found.values())


def find_contacts_by_secondary(
    client: KommoClient, key: str, value: str
) -> list[dict[str, Any]]:
    """Поиск по вторичному ключу (когда телефона нет). Точная сверка значения
    поля, чтобы query не притянул лишнее."""
    if not value:
        return []
    fid = SECONDARY_FIELDS.get(key)
    if not fid:
        return []
    found: dict[int, dict[str, Any]] = {}
    for c in client.search_contacts(value, limit=20):
        if value in _cf_values(c, fid):
            found[c["id"]] = c
    return list(found.values())
