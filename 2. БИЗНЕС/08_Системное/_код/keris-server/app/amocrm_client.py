"""Прямая интеграция с amoCRM (без штатного коннектора YCLIENTS<->amoCRM).

Авторизация — статичный долгосрочный токен (см. ДОСТУПЫ.md, Этап 0),
без OAuth refresh-цикла. Если токен недействителен, вызовы логируются и
уходят в sync_log как retry — запись у нас всё равно проходит успешно.

Модель данных (АРХИТЕКТУРА_ЭКОСИСТЕМЫ.md, Принцип №2):

    Контакт  = владелец питомца
    Компания = питомец клиента
    Сделка   = один визит груминга в воронке «Груминг»

ID этапов и кастомных полей здесь не хардкодятся: они резолвятся по имени при
первом обращении и кэшируются на процесс. Воронку и поля создаёт
`keris-amocrm-scripts/setup_grooming_pipeline.py`, он же печатает фактические ID
для `ДОСТУПЫ.md`.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .config import settings

log = logging.getLogger("keris.amocrm")

# Системные этапы есть в любой воронке и переименование через API недоступно,
# поэтому целимся в них по номеру.
STATUS_WON = 142
STATUS_LOST = 143


class AmoCrmError(Exception):
    pass


class AmoCrmNotConfigured(AmoCrmError):
    pass


class AmoCrmBlocked(AmoCrmError):
    """Аккаунт не даёт создавать сущности: подписка не оплачена.

    Проверено 22.08.2026 на живом аккаунте: чтение, настройки воронок и полей,
    примечания — работают, а `POST /leads` отвечает «Payment Required», `POST
    /contacts` и `/companies` — «Код ошибки 205». Обычная ошибка интеграции это
    не лечит, поэтому дальше долбить аккаунт бессмысленно.
    """


# Пока аккаунт заблокирован, запросы не отправляются: иначе пятиминутный проход
# по всем живым записям каждый раз ходит в сеть впустую и заливает журнал.
BLOCK_COOLDOWN_SEC = 1800
_blocked_until = 0.0


def blocked() -> bool:
    return time.monotonic() < _blocked_until


def _mark_blocked(detail: str) -> None:
    global _blocked_until
    if not blocked():
        log.warning("amoCRM не принимает записи (подписка): %s. Пауза %d мин, "
                    "сделки доберёт проход после оплаты", detail, BLOCK_COOLDOWN_SEC // 60)
    _blocked_until = time.monotonic() + BLOCK_COOLDOWN_SEC


def _headers() -> dict[str, str]:
    if not settings.amocrm_ready:
        raise AmoCrmNotConfigured("AMOCRM_LONG_LIVED_TOKEN не задан")
    return {
        "Authorization": f"Bearer {settings.amocrm_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


_BLOCKED_MARKERS = ("Payment Required", "Код ошибки 205", "Not enough rights")


def _request(method: str, path: str, **kwargs: Any) -> dict:
    if blocked() and method != "GET":
        raise AmoCrmBlocked(f"{method} {path}: аккаунт amoCRM не принимает записи")
    url = f"{settings.amocrm_base_url}{path}"
    resp = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)
    if resp.status_code == 204 or not resp.content:
        return {}
    if resp.status_code >= 500 or resp.status_code == 429:
        raise AmoCrmError(f"{method} {path} -> {resp.status_code} (retryable): {resp.text[:300]}")
    if resp.status_code >= 400:
        text = resp.text[:300]
        if any(marker in resp.text for marker in _BLOCKED_MARKERS):
            _mark_blocked(f"{method} {path} -> {text}")
            raise AmoCrmBlocked(f"{method} {path} -> {resp.status_code}: {text}")
        raise AmoCrmError(f"{method} {path} -> {resp.status_code}: {text}")
    try:
        return resp.json()
    except ValueError:
        return {}


def check_account() -> dict:
    return _request("GET", "/api/v4/account")


# ---------------------------------------------------------------------------
# Резолв этапов и полей по имени (кэш на процесс)
# ---------------------------------------------------------------------------

_stage_cache: dict[str, int] = {}
_field_cache: dict[str, dict[str, dict]] = {}


def reset_cache() -> None:
    """Сбросить кэш: после переименования этапа/поля в интерфейсе amoCRM."""
    _stage_cache.clear()
    _field_cache.clear()


def grooming_stages() -> dict[str, int]:
    """{имя этапа в нижнем регистре: status_id} воронки «Груминг»."""
    if _stage_cache:
        return _stage_cache
    pipeline_id = settings.amocrm_pipeline_grooming_id
    if not pipeline_id:
        raise AmoCrmNotConfigured("AMOCRM_PIPELINE_GROOMING_ID не задан")
    data = _request("GET", f"/api/v4/leads/pipelines/{int(pipeline_id)}")
    for status in ((data or {}).get("_embedded") or {}).get("statuses") or []:
        name = str(status.get("name") or "").strip().lower()
        if name and isinstance(status.get("id"), int):
            _stage_cache[name] = status["id"]
    return _stage_cache


# Системные этапы API переименовывать не даёт, а в интерфейсе их переименовать
# можно (в воронке «Продажи» Успех называется «Уехал в семью»). Поэтому Успех и
# Провал ищутся не только по фактическому названию, но и по любому из псевдонимов.
_WON_ALIASES = {"визит завершён", "визит завершен", "успешно реализовано", "успех"}
_LOST_ALIASES = {"провал", "закрыто и не реализовано"}


def stage_id(name: str) -> int | None:
    """status_id этапа по имени. Системный Успех/Провал — по номеру."""
    try:
        stages = grooming_stages()
    except AmoCrmError:
        log.warning("не удалось получить этапы воронки «Груминг»", exc_info=True)
        return None
    key = name.strip().lower()
    found = stages.get(key)
    if found is not None:
        return found
    if key in _WON_ALIASES:
        return STATUS_WON
    if key in _LOST_ALIASES:
        return STATUS_LOST
    log.warning("этап «%s» не найден в воронке «Груминг» — сделка останется на текущем", name)
    return None


def _fields(entity: str) -> dict[str, dict]:
    """{имя поля в нижнем регистре: описание поля} для leads/contacts/companies."""
    cached = _field_cache.get(entity)
    if cached is not None:
        return cached
    result: dict[str, dict] = {}
    page = 1
    while True:
        data = _request("GET", f"/api/v4/{entity}/custom_fields",
                        params={"page": page, "limit": 250})
        items = ((data or {}).get("_embedded") or {}).get("custom_fields") or []
        if not items:
            break
        for item in items:
            name = str(item.get("name") or "").strip().lower()
            if name:
                result[name] = item
        if len(items) < 250:
            break
        page += 1
    _field_cache[entity] = result
    return result


def field_id(entity: str, name: str) -> int | None:
    try:
        return (_fields(entity).get(name.strip().lower()) or {}).get("id")
    except AmoCrmError:
        log.warning("не удалось получить поля %s", entity, exc_info=True)
        return None


def _enum_id(entity: str, name: str, value: str) -> int | None:
    """enum_id значения select-поля: amoCRM принимает и текст, но по enum_id
    надёжнее — при опечатке в тексте поле молча остаётся пустым."""
    field = _fields(entity).get(name.strip().lower()) or {}
    for enum in field.get("enums") or []:
        if str(enum.get("value") or "").strip().lower() == value.strip().lower():
            return enum.get("id")
    return None


# Этот аккаунт отклоняет `Y-m-d` на полях date/date_time (400 InvalidDateFormat)
# и требует `Y-m-dTH:i:sP`. Unix timestamp тоже проходит, но явный ISO надёжнее:
# не зависит от того, как amoCRM трактует «дату без времени».
_MOSCOW = timezone(timedelta(hours=3))


def amo_datetime(value: Any, with_time: bool = False) -> str | None:
    """Дата для кастомного поля: `2026-08-10T00:00:00+03:00`."""
    if value is None or value == "" or value is False:
        return None
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=_MOSCOW)
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=_MOSCOW)
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(int(value), tz=_MOSCOW)
    else:
        raw = str(value).strip()
        if raw.isdigit():
            dt = datetime.fromtimestamp(int(raw), tz=_MOSCOW)
        else:
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw[:19] if "T" in raw else raw, fmt)
                    dt = parsed.replace(tzinfo=_MOSCOW)
                    break
                except ValueError:
                    continue
            if dt is None:
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=_MOSCOW)
                except ValueError:
                    log.warning("не разобрал дату «%s» для amoCRM", raw)
                    return None
    if not with_time:
        dt = dt.astimezone(_MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.isoformat(timespec="seconds")


def custom_field(entity: str, name: str, value: Any) -> dict | None:
    """Одно значение кастомного поля в формате amoCRM. None — поля нет или
    значение пустое (тогда поле в запрос не попадает и ничего не затирает)."""
    if value is None or value == "":
        return None
    fid = field_id(entity, name)
    if fid is None:
        log.warning("поле «%s» (%s) не найдено в amoCRM — пропущено", name, entity)
        return None
    field = _fields(entity).get(name.strip().lower()) or {}
    ftype = field.get("type")
    if ftype == "select":
        enum_id = _enum_id(entity, name, str(value))
        if enum_id is None:
            log.warning("значение «%s» не найдено в списке поля «%s»", value, name)
            return None
        return {"field_id": int(fid), "values": [{"enum_id": enum_id}]}
    if ftype in ("date", "date_time"):
        formatted = amo_datetime(value, with_time=(ftype == "date_time"))
        if formatted is None:
            return None
        return {"field_id": int(fid), "values": [{"value": formatted}]}
    return {"field_id": int(fid), "values": [{"value": value}]}


def custom_fields(entity: str, values: dict[str, Any]) -> list[dict]:
    out = []
    for name, value in values.items():
        item = custom_field(entity, name, value)
        if item is not None:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Контакт-владелец
# ---------------------------------------------------------------------------

def phone_variants(phone: str) -> list[str]:
    """Один и тот же номер в записанных вариантах: +79993689871, 89993689871,
    79993689871, 9993689871. В аккаунте уже 250+ контактов из Wazzup, формат
    у них разный — поиск обязан находить существующего, а не плодить дубли."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return [str(phone or "").strip()] if phone else []
    return [f"+7{digits}", f"8{digits}", f"7{digits}", digits]


def _contact_phones(contact: dict) -> list[str]:
    out = []
    for field in contact.get("custom_fields_values") or []:
        if (field or {}).get("field_code") != "PHONE":
            continue
        for value in field.get("values") or []:
            raw = str((value or {}).get("value") or "")
            digits = "".join(ch for ch in raw if ch.isdigit())
            if digits:
                out.append(digits)
    return out


def find_contact_by_phone(phone: str) -> dict | None:
    """Контакт с этим телефоном — со сверкой поля PHONE, а не «первый из поиска».

    `GET /contacts?query=` ищет по всем полям и на общем ключевом слове может
    вернуть чужую карточку, поэтому результат проверяется по цифрам номера.
    """
    variants = phone_variants(phone)
    if not variants:
        return None
    target = {"".join(ch for ch in v if ch.isdigit())[-10:] for v in variants}
    for variant in variants:
        data = _request("GET", "/api/v4/contacts",
                        params={"query": variant, "limit": 25, "with": "leads"})
        contacts = ((data or {}).get("_embedded") or {}).get("contacts") or []
        for contact in contacts:
            for digits in _contact_phones(contact):
                if digits[-10:] in target:
                    return contact
    return None


def get_contact(contact_id: int) -> dict:
    return _request("GET", f"/api/v4/contacts/{int(contact_id)}")


def has_field_value(item: dict, entity: str, name: str) -> bool:
    """Заполнено ли поле в уже полученной карточке. Нужно для «изначальных»
    значений (источник, дата согласия), которые нельзя перезаписывать."""
    fid = field_id(entity, name)
    if fid is None:
        return True  # поля нет — писать всё равно некуда
    for field in item.get("custom_fields_values") or []:
        if (field or {}).get("field_id") != fid:
            continue
        for value in field.get("values") or []:
            if (value or {}).get("value") not in (None, "", False):
                return True
    return False


def create_contact(name: str, phone: str, fields: dict[str, Any] | None = None) -> int | None:
    cfs = [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}]
    cfs.extend(custom_fields("contacts", fields or {}))
    body = [{"name": name or phone, "custom_fields_values": cfs}]
    data = _request("POST", "/api/v4/contacts", json=body)
    contacts = ((data or {}).get("_embedded") or {}).get("contacts") or []
    return contacts[0]["id"] if contacts else None


def update_contact(contact_id: int, fields: dict[str, Any], name: str = "") -> None:
    """Только переданные поля. Пустые значения отбрасываются в custom_fields —
    метрика, которую ещё не посчитали, не должна затирать заполненную."""
    body: dict[str, Any] = {}
    cfs = custom_fields("contacts", fields)
    if cfs:
        body["custom_fields_values"] = cfs
    if name:
        body["name"] = name
    if not body:
        return
    _request("PATCH", f"/api/v4/contacts/{int(contact_id)}", json=body)


def find_or_create_contact(name: str, phone: str, fields: dict[str, Any] | None = None) -> int | None:
    existing = find_contact_by_phone(phone)
    if existing and isinstance(existing.get("id"), int):
        if fields:
            try:
                update_contact(existing["id"], fields)
            except AmoCrmError:
                log.warning("не удалось дописать поля контакту %s", existing["id"], exc_info=True)
        return existing["id"]
    return create_contact(name, phone, fields)


# ---------------------------------------------------------------------------
# Компания = питомец клиента
# ---------------------------------------------------------------------------

def find_pet_company(contact_id: int, pet_name: str) -> int | None:
    """Карточка питомца этого владельца по кличке.

    Ищем среди компаний, привязанных к контакту, а не по всему аккаунту: «Боня»
    может быть у нескольких владельцев, и общий поиск связал бы визит с чужой собакой.
    """
    if not pet_name:
        return None
    data = _request("GET", f"/api/v4/contacts/{int(contact_id)}", params={"with": "companies"})
    companies = ((data or {}).get("_embedded") or {}).get("companies") or []
    wanted = pet_name.strip().lower()
    for company in companies:
        cid = company.get("id")
        if not isinstance(cid, int):
            continue
        full = _request("GET", f"/api/v4/companies/{cid}")
        if str(full.get("name") or "").strip().lower() == wanted:
            return cid
    return None


def create_pet_company(contact_id: int, pet_name: str, fields: dict[str, Any]) -> int | None:
    item: dict[str, Any] = {
        "name": pet_name,
        "_embedded": {"contacts": [{"id": int(contact_id)}]},
    }
    cfs = custom_fields("companies", fields)
    if cfs:
        item["custom_fields_values"] = cfs
    data = _request("POST", "/api/v4/companies", json=[item])
    companies = ((data or {}).get("_embedded") or {}).get("companies") or []
    return companies[0]["id"] if companies else None


def update_pet_company(company_id: int, fields: dict[str, Any]) -> None:
    cfs = custom_fields("companies", fields)
    if not cfs:
        return
    _request("PATCH", f"/api/v4/companies/{int(company_id)}", json={"custom_fields_values": cfs})


def find_or_create_pet_company(contact_id: int, pet_name: str,
                                fields: dict[str, Any] | None = None) -> int | None:
    existing = find_pet_company(contact_id, pet_name)
    if existing is not None:
        if fields:
            try:
                update_pet_company(existing, fields)
            except AmoCrmError:
                log.warning("не удалось обновить карточку питомца %s", existing, exc_info=True)
        return existing
    return create_pet_company(contact_id, pet_name, fields or {})


# ---------------------------------------------------------------------------
# Сделка-визит
# ---------------------------------------------------------------------------

def create_grooming_lead(*, contact_id: int, booking_summary: str, price: int,
                          status: str = "", fields: dict[str, Any] | None = None,
                          company_id: int | None = None) -> int | None:
    """Сделка-визит в воронке «Груминг», привязанная к владельцу и питомцу."""
    embedded: dict[str, Any] = {"contacts": [{"id": int(contact_id), "is_main": True}]}
    if company_id:
        embedded["companies"] = [{"id": int(company_id)}]
    item: dict[str, Any] = {
        "name": booking_summary,
        "price": int(price),
        "_embedded": embedded,
    }
    if settings.amocrm_pipeline_grooming_id:
        item["pipeline_id"] = int(settings.amocrm_pipeline_grooming_id)
    if status:
        sid = stage_id(status)
        if sid is not None:
            item["status_id"] = sid
    cfs = custom_fields("leads", fields or {})
    if cfs:
        item["custom_fields_values"] = cfs
    data = _request("POST", "/api/v4/leads", json=[item])
    leads = ((data or {}).get("_embedded") or {}).get("leads") or []
    return leads[0]["id"] if leads else None


def update_grooming_lead(lead_id: int, *, price: int | None = None,
                          fields: dict[str, Any] | None = None,
                          company_id: int | None = None,
                          name: str = "") -> None:
    body: dict[str, Any] = {}
    if price is not None:
        body["price"] = int(price)
    if name:
        body["name"] = name
    if company_id:
        body["_embedded"] = {"companies": [{"id": int(company_id)}]}
    cfs = custom_fields("leads", fields or {})
    if cfs:
        body["custom_fields_values"] = cfs
    if not body:
        return
    _request("PATCH", f"/api/v4/leads/{int(lead_id)}", json=body)


def move_lead_to_stage(lead_id: int, status: str) -> bool:
    """Перевод сделки на этап по имени. Успех/Провал — по номеру статуса."""
    sid = stage_id(status)
    if sid is None:
        return False
    body: dict[str, Any] = {"status_id": sid}
    if settings.amocrm_pipeline_grooming_id:
        body["pipeline_id"] = int(settings.amocrm_pipeline_grooming_id)
    _request("PATCH", f"/api/v4/leads/{int(lead_id)}", json=body)
    return True


def add_lead_note(lead_id: int, text: str) -> None:
    """Примечание в ленту сделки — хронология визита читается глазами,
    рядом с перепиской из Wazzup на карточке контакта."""
    if not text:
        return
    _request("POST", f"/api/v4/leads/{int(lead_id)}/notes", json=[{
        "note_type": "common",
        "params": {"text": text[:2000]},
    }])


def create_puppy_lead(*, name: str, fields: dict[str, Any]) -> int | None:
    """Карточка щенка (используется админ-ботом Карины «Добавить щенка»).

    Реюз пайплайна «Щенки» Этапа 0 (см. ДОСТУПЫ.md / ТЗ_ЭТАП0_ПИТОМНИК.md §6.2).
    Здесь поля приходят уже с field_id (их печатает setup_amocrm.py), а не по имени.
    """
    item: dict[str, Any] = {"name": name}
    cfs = []
    for fid, value in fields.items():
        if not fid:
            continue
        cfs.append({"field_id": int(fid), "values": [{"value": value}]})
    if cfs:
        item["custom_fields_values"] = cfs
    data = _request("POST", "/api/v4/leads", json=[item])
    leads = ((data or {}).get("_embedded") or {}).get("leads") or []
    return leads[0]["id"] if leads else None
