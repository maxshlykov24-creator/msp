"""Клиент YCLIENTS API (developer.yclients.com).

Авторизация: `Authorization: Bearer <partner_token>, User <user_token>`.
User Token: YCLIENTS -> «Мои приложения» -> «Доступ к API» (или /auth
логином+паролем компании). Без него все вызовы падают на этапе логирования,
без исключения наверх — запись у нас проходит, sync просто уходит в retry-лог
(see sync.py) до появления валидных токенов.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import settings

log = logging.getLogger("keris.yclients")

BASE_URL = "https://api.yclients.com/api/v1"


class YClientsError(Exception):
    pass


class YClientsNotConfigured(YClientsError):
    pass


def _headers() -> dict[str, str]:
    if not settings.yclients_ready:
        raise YClientsNotConfigured(
            "YCLIENTS не настроен: нужны YCLIENTS_PARTNER_TOKEN, YCLIENTS_USER_TOKEN, YCLIENTS_COMPANY_ID"
        )
    return {
        "Accept": "application/vnd.yclients.v2+json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.yclients_partner_token}, User {settings.yclients_user_token}",
    }


def _request(method: str, path: str, **kwargs: Any) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, headers=_headers(), timeout=15, **kwargs)
    if resp.status_code >= 500 or resp.status_code == 429:
        raise YClientsError(f"{method} {path} -> {resp.status_code} (retryable): {resp.text[:300]}")
    if resp.status_code >= 400:
        raise YClientsError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError:
        return {}


def get_services(company_id: str | None = None) -> list[dict]:
    cid = company_id or settings.yclients_company_id
    data = _request("GET", f"/company/{cid}/services")
    return data.get("data", [])


def get_service_categories(company_id: str | None = None) -> list[dict]:
    cid = company_id or settings.yclients_company_id
    data = _request("GET", f"/service_categories/{cid}")
    return data.get("data", [])


def create_service_category(title: str, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("POST", f"/service_categories/{cid}", json={"title": title})


def update_service_category(category_id: int, title: str, company_id: str | None = None) -> dict:
    # Путь в единственном числе (service_category) — отличается от create (service_categories, мн.ч.).
    cid = company_id or settings.yclients_company_id
    return _request("PUT", f"/service_category/{cid}/{category_id}", json={"title": title})


def create_service(payload: dict, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("POST", f"/services/{cid}", json=payload)


def update_service(service_id: int, payload: dict, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("PUT", f"/services/{cid}/{service_id}", json=payload)


def delete_service(service_id: int, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("DELETE", f"/services/{cid}/{service_id}")


def get_staff(company_id: str | None = None) -> list[dict]:
    cid = company_id or settings.yclients_company_id
    data = _request("GET", f"/company/{cid}/staff")
    return data.get("data", [])


def ensure_staff_on_services(staff_ids: list[int], service_ids: set[int] | None = None) -> dict:
    """Добавляет сотрудников в `staff` услуг, не снимая уже привязанных.

    Новый мастер в журнале YCLIENTS без привязки к услугам не принимает запись:
    `is_bookable` остаётся false, а наш push отвечает 409. Сюда попадают только
    id из нашего каталога, чужие заготовки филиала не трогаем.
    """
    wanted = [int(sid) for sid in staff_ids if sid]
    if not wanted:
        return {"updated": 0, "skipped": 0}
    updated = 0
    skipped = 0
    for svc in get_services():
        yc_id = svc.get("id")
        if not yc_id:
            continue
        if service_ids is not None and int(yc_id) not in service_ids:
            skipped += 1
            continue
        current = list(svc.get("staff") or [])
        have = {int(row["id"]) for row in current if row.get("id")}
        missing = [sid for sid in wanted if sid not in have]
        if not missing:
            continue
        seance = 300
        if current and current[0].get("seance_length"):
            seance = int(current[0]["seance_length"])
        elif svc.get("duration"):
            seance = int(svc["duration"])
        staff = [
            {"id": int(row["id"]), "seance_length": int(row.get("seance_length") or seance)}
            for row in current if row.get("id")
        ]
        for sid in missing:
            staff.append({"id": sid, "seance_length": seance})
        update_service(int(yc_id), {
            "title": svc.get("title") or "",
            "category_id": svc.get("category_id"),
            "price_min": svc.get("price_min") or 0,
            "price_max": svc.get("price_max") or 0,
            "duration": svc.get("duration") or seance,
            "comment": (svc.get("comment") or "")[:255],
            "staff": staff,
            "is_online": 1,
            "active": 1 if svc.get("active") else 0,
        })
        updated += 1
        time.sleep(0.12)
    return {"updated": updated, "skipped": skipped}


def get_book_times(staff_id: int, date_iso: str, company_id: str | None = None) -> list[dict]:
    cid = company_id or settings.yclients_company_id
    data = _request("GET", f"/book_times/{cid}/{staff_id}/{date_iso}")
    return data.get("data", [])


def get_staff_schedule(staff_ids: list[int], start_date: str, end_date: str,
                       company_id: str | None = None) -> list[dict]:
    """График мастеров за период: `[{"staff_id":1,"date":"2026-08-20","slots":[{"from","to"}]}]`.

    В ответе только те дни, где смена выставлена. Дня нет в ответе — мастер в этот
    день не работает, и запись на него YCLIENTS не примет.
    """
    cid = company_id or settings.yclients_company_id
    query = "&".join([f"staff_ids[]={sid}" for sid in staff_ids])
    path = f"/company/{cid}/staff/schedule?{query}&start_date={start_date}&end_date={end_date}"
    data = _request("GET", path)
    return data.get("data", [])


def set_staff_schedule(schedules_to_set: list[dict], schedules_to_delete: list[dict] | None = None,
                        company_id: str | None = None) -> dict:
    """График работы мастеров в YCLIENTS = проекция нашего графика (Master.shift_*,
    MasterDayOff, SalonClosure). Пока у сотрудника нет графика, он `is_bookable:false`
    и запись с карт на него невозможна.

    schedules_to_set:    [{"staff_id": 1, "dates": ["2026-08-10"], "slots": [{"from": "10:00", "to": "22:00"}]}]
    schedules_to_delete: [{"staff_id": 1, "dates": ["2026-08-11"]}]
    """
    cid = company_id or settings.yclients_company_id
    payload: dict[str, Any] = {"schedules_to_set": schedules_to_set}
    if schedules_to_delete:
        payload["schedules_to_delete"] = schedules_to_delete
    return _request("PUT", f"/company/{cid}/staff/schedule", json=payload)


def create_record(payload: dict, company_id: str | None = None) -> dict:
    """Пуш записи, созданной у нас, в YCLIENTS (book_record).

    payload — минимальный набор полей YCLIENTS record: staff_id, services,
    client (имя/телефон), datetime, seance_length (сек), comment.
    """
    cid = company_id or settings.yclients_company_id
    return _request("POST", f"/records/{cid}", json=payload)


def update_record(record_id: int, payload: dict, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("PUT", f"/record/{cid}/{record_id}", json=payload)


def get_record(record_id: int, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("GET", f"/record/{cid}/{record_id}")


def delete_record(record_id: int, company_id: str | None = None) -> dict:
    cid = company_id or settings.yclients_company_id
    return _request("DELETE", f"/record/{cid}/{record_id}")


# attendance в YCLIENTS: -1 не пришёл, 0 ожидание, 1 пришёл, 2 подтвердил.
ATTENDANCE_NO_SHOW = -1
ATTENDANCE_WAITING = 0
ATTENDANCE_CAME = 1
ATTENDANCE_CONFIRMED = 2


def mark_record_no_show(record_id: int, payload: dict, company_id: str | None = None) -> dict:
    """Отмена записи так, как это делает администратор в журнале: статус «Не пришел»,
    а не удаление. Слот в YCLIENTS освобождается, история клиента сохраняется
    (см. инструкция/Администратор_YCLIENTS.html и регламент_Администратор.md)."""
    return update_record(record_id, {**payload, "attendance": ATTENDANCE_NO_SHOW}, company_id)


def confirm_record(record_id: int, payload: dict, company_id: str | None = None) -> dict:
    """Клиент нажал «Подтверждаю» в напоминании. PUT /record требует полный набор
    полей (staff_id/services/client/datetime/seance_length), иначе YCLIENTS 422 —
    поэтому payload собирается так же, как для update_record (booking_to_yclients_payload),
    и мы только добавляем к нему attendance."""
    return update_record(record_id, {**payload, "attendance": ATTENDANCE_CONFIRMED}, company_id)


# Лимит поля comment у record YCLIENTS на практике узкий; id в конце не обрезаем.
_COMMENT_MAX = 255


def format_yclients_comment(booking) -> str:
    """Комментарий записи в YCLIENTS: только реальный текст клиента + служебные
    флаги, для которых в YCLIENTS нет отдельного доп. поля (абонемент, согласие
    на фото). Id (`KERIS-…`) — в скобках, для ручного поиска в админке/поддержке.

    Кличка/порода/вес/дата рождения питомца сюда больше не пишутся — с 2026-08-17
    они уходят отдельными доп. полями записи, см. `pet_custom_fields()`. Имя/
    телефон/услуга/время у YCLIENTS и так в своих полях. Связка sync ↔ наша БД —
    по `yclients_record_id`, не по этому тексту.
    """
    parts: list[str] = []

    client_note = (getattr(booking, "comment", None) or "").strip()
    if client_note:
        parts.append(client_note)

    if getattr(booking, "subscription_id", None):
        charged = getattr(booking, "visits_charged", None)
        if charged:
            parts.append(f"абонемент, списано {charged:g}")
        else:
            parts.append("абонемент")

    if not getattr(booking, "media_consent", True):
        parts.append("нет согласия на фото")

    body = ". ".join(p for p in parts if p).strip()
    booking_id = (getattr(booking, "id", None) or "").strip()
    if booking_id:
        suffix = f" ({booking_id})"
        # тело режем так, чтобы суффикс с id всегда влез
        max_body = max(0, _COMMENT_MAX - len(suffix))
        if len(body) > max_body:
            body = body[: max(0, max_body - 1)].rstrip() + "…"
        return (body + suffix) if body else suffix.strip()
    return body[:_COMMENT_MAX]


# Код доп. поля записи YCLIENTS ("Ключ для API") -> как достать значение из Booking.
_PET_FIELD_GETTERS: dict[str, str] = {
    "yclients_cf_pet_name": "pet_name",
    "yclients_cf_pet_breed": "pet_breed",
    "yclients_cf_pet_weight": "pet_weight_kg",
    "yclients_cf_pet_birth_date": "pet_birth_date",
}


def pet_custom_fields(booking) -> dict[str, object]:
    """Значения доп. полей записи YCLIENTS (кличка/порода/вес/дата рождения) из
    нашей записи, ключи — коды полей из `Settings` (`yclients_cf_pet_*`).

    Поле без настроенного кода или с пустым значением у нас в payload не идёт —
    так PUT записи не затирает то, что вручную заполнил администратор в YCLIENTS,
    если у нас этого значения ещё/уже нет.
    """
    fields: dict[str, object] = {}
    for setting_name, attr in _PET_FIELD_GETTERS.items():
        code = getattr(settings, setting_name, "")
        if not code:
            continue
        value = getattr(booking, attr, None)
        if value is None:
            continue
        if isinstance(value, float):
            fields[code] = value
        else:
            text = str(value).strip()
            if text:
                fields[code] = text
    return fields


def extract_custom_fields(data: dict) -> dict[str, object]:
    """Доп. поля записи из вебхука/GET record. YCLIENTS документирует формат
    ключ-значение (`{"code": value}`), но на практике пустой набор иногда
    приходит списком (`[]`), а заполненный — списком объектов `{code, value}`
    (как для клиента) — обрабатываем оба варианта."""
    raw = data.get("custom_fields")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        result: dict[str, object] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            code = item.get("code") or item.get("api_key") or item.get("key")
            if code:
                result[str(code)] = item.get("value")
        return result
    return {}


def booking_to_yclients_payload(
    booking,
    master_yclients_staff_id: int,
    service_yclients_id: int,
    addon_yclients_ids: list[int] | None = None,
) -> dict:
    services = [{"id": service_yclients_id}]
    for aid in addon_yclients_ids or []:
        if aid and aid != service_yclients_id:
            services.append({"id": aid})
    payload: dict[str, Any] = {
        "staff_id": master_yclients_staff_id,
        "services": services,
        "client": {"name": booking.owner_name, "phone": booking.owner_phone},
        "datetime": booking.starts_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "seance_length": int((booking.ends_at - booking.starts_at).total_seconds()),
        "comment": format_yclients_comment(booking),
    }
    custom_fields = pet_custom_fields(booking)
    if custom_fields:
        payload["custom_fields"] = custom_fields
    return payload
