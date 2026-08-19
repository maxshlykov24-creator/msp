from __future__ import annotations

import logging
from typing import Any, Optional, Union

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.phone_utils import normalize_tracking
from app.tokens import get_valid_access_token

log = logging.getLogger(__name__)

_cf_value = Union[str, bool, int]


def _base_url() -> str:
    s = get_settings()
    return f"https://{s.amo_subdomain}.{s.amo_base_domain}"


def _cf_value_payload(value: _cf_value) -> _cf_value:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    return str(value)


def _headers(db: Session) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_valid_access_token(db)}",
        "Content-Type": "application/json",
        "Accept": "application/hal+json",
    }


def _entity_cdek_matches(entity: dict[str, Any], field_id: int, want_norm: str) -> bool:
    if field_id <= 0:
        return False
    for grp in entity.get("custom_fields_values") or []:
        if grp.get("field_id") != field_id:
            continue
        for v in grp.get("values") or []:
            val = str(v.get("value") or "")
            if normalize_tracking(val) == want_norm:
                return True
    return False


def _any_custom_value_matches_normalized(entity: dict[str, Any], want_norm: str) -> bool:
    for grp in entity.get("custom_fields_values") or []:
        for v in grp.get("values") or []:
            val = str(v.get("value") or "")
            if normalize_tracking(val) == want_norm:
                return True
    return False


def _build_cf_patch(field_id: int, value: _cf_value) -> dict[str, Any]:
    return {"field_id": field_id, "values": [{"value": _cf_value_payload(value)}]}


def search_contacts_by_query(db: Session, query: str) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    url = f"{_base_url()}/api/v4/contacts"
    variants: list[str] = []
    q0 = query.strip()
    variants.append(q0)
    if q0.isdigit() and q0.startswith("7") and len(q0) == 11:
        variants.append("+" + q0)
    seen: dict[int, dict[str, Any]] = {}
    with httpx.Client(timeout=30.0) as client:
        for v in variants:
            params: list[tuple[str, str]] = [("query", v), ("limit", "50")]
            r = client.get(url, params=params, headers=_headers(db))
            # amoCRM отдаёт 204 без тела, когда совпадений нет — это не ошибка.
            if r.status_code == 204 or not r.content:
                continue
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                continue
            emb = (data or {}).get("_embedded") or {}
            items = emb.get("contacts") or []
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("id"), int):
                    seen[int(it["id"])] = it
    return list(seen.values())


def create_contact_phone(db: Session, *, phone_international_plus: str) -> Optional[int]:
    """
    Новый контакт с телефоном (поле по field_code PHONE — типично для amoCRM).
    phone_international_plus: например +79991234567
    """
    url = f"{_base_url()}/api/v4/contacts"
    body = [
        {
            "custom_fields_values": [
                {
                    "field_code": "PHONE",
                    "values": [{"value": phone_international_plus, "enum_code": "WORK"}],
                }
            ],
        }
    ]
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=_headers(db), json=body)
        if r.status_code >= 400:
            log.warning("create_contact failed: %s %s", r.status_code, r.text[:800])
            return None
        data = r.json()
    if isinstance(data, dict):
        elems = data.get("_embedded", {}).get("contacts") if data.get("_embedded") else None
        if isinstance(elems, list) and elems:
            cid = elems[0].get("id")
            if isinstance(cid, int):
                return cid
        # иногда amo возвращает список id в links или по-другому
        ids = data.get("id")
        if isinstance(ids, int):
            return ids
    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("id"), int):
            return int(first["id"])
    log.warning("create_contact: unexpected body %s", str(data)[:500])
    return None


def get_contact_phones(db: Session, contact_id: int) -> list[str]:
    """Возвращает все значения поля PHONE (multitext) у контакта.

    У контакта в amoCRM может быть несколько телефонов: один для доставки,
    другой — личный/для мессенджеров. Telegram-binding мог быть оформлен
    под любой из них, поэтому сопоставление по контакту → telegram-чат
    должно учитывать ВСЕ телефоны карточки.
    """
    cid = int(contact_id or 0)
    if cid <= 0:
        return []
    url = f"{_base_url()}/api/v4/contacts/{cid}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(db))
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            log.warning("get_contact_phones %s -> %s %s", cid, r.status_code, r.text[:300])
            return []
        try:
            data = r.json()
        except Exception:
            return []
    out: list[str] = []
    for grp in data.get("custom_fields_values") or []:
        if (grp.get("field_code") or "").upper() != "PHONE":
            continue
        for v in grp.get("values") or []:
            val = v.get("value")
            if val is None:
                continue
            s = str(val).strip()
            if s:
                out.append(s)
    return out


def patch_contact_fields(db: Session, contact_id: int, fields: dict[int, _cf_value]) -> None:
    s = get_settings()
    cfs: list[dict[str, Any]] = []
    for fid, val in fields.items():
        if fid <= 0 or val is None:
            continue
        cfs.append(_build_cf_patch(fid, val))
    if not cfs:
        return
    url = f"{_base_url()}/api/v4/contacts"
    body = [{"id": int(contact_id), "custom_fields_values": cfs}]
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(url, headers=_headers(db), json=body)
        if r.status_code >= 400:
            log.warning("patch_contact_fields %s: %s %s", contact_id, r.status_code, r.text[:800])


def _fetch_leads(
    db: Session,
    params: Optional[list[tuple[str, Any]]],
) -> list[dict[str, Any]]:
    url = f"{_base_url()}/api/v4/leads"
    q: list[tuple[str, Any]] = [("limit", "250"), ("with", "contacts")]
    if params:
        q.extend(params)
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(db), params=q)
        if r.status_code == 204:
            return []
        if r.status_code >= 400:
            log.warning(
                "amo /api/v4/leads %s -> %s body[:300]=%s",
                q,
                r.status_code,
                r.text[:300],
            )
            return []
        try:
            data = r.json()
        except Exception:
            log.warning("amo /api/v4/leads: non-JSON body[:300]=%s", r.text[:300])
            return []
    emb = (data or {}).get("_embedded") or {}
    ls = emb.get("leads") or []
    return ls if isinstance(ls, list) else []


def find_leads_with_cdek(db: Session, tracking_raw: str) -> list[dict[str, Any]]:
    """Находим сделки с заполненным «Номер СДЭК» = tracking_raw.

    amoCRM не поддерживает фильтр /api/v4/leads по custom_fields_values
    (отвечает 400 Bad Request). Единственный рабочий путь — полнотекстовый
    поиск по сделке (`?query=...`), который индексирует в т.ч. текстовые
    custom-поля. Возвращённые лиды дополнительно валидируем по точному
    совпадению нормализованного трека.
    """
    s = get_settings()
    fid = int(s.amo_field_lead_cdek or 0)
    want_norm = normalize_tracking(tracking_raw)
    if not want_norm:
        return []

    found: dict[int, dict[str, Any]] = {}

    queries = {tracking_raw, tracking_raw.strip(), want_norm}
    for q in queries:
        if not str(q).strip():
            continue
        try:
            leads = _fetch_leads(db, [("query", str(q))])
        except Exception:
            log.warning("amo leads ?query=%s failed", str(q)[:30], exc_info=True)
            continue
        for lead in leads:
            lid = lead.get("id")
            if not isinstance(lid, int) or lid in found:
                continue
            if fid > 0:
                matched = _entity_cdek_matches(lead, fid, want_norm)
            else:
                matched = _any_custom_value_matches_normalized(lead, want_norm)
            if matched:
                found[lid] = lead

    return list(found.values())


def fetch_lead_by_id(db: Session, lead_id: int) -> Optional[dict[str, Any]]:
    """GET /api/v4/leads/{id} — актуальные custom_fields (после нативного обновления LI → amo)."""
    lid = int(lead_id or 0)
    if lid <= 0:
        return None
    url = f"{_base_url()}/api/v4/leads/{lid}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(db))
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            log.warning("amo GET /leads/%s -> %s body[:200]=%s", lid, r.status_code, r.text[:200])
            return None
        try:
            data = r.json()
        except Exception:
            log.warning("amo GET /leads/%s: non-JSON", lid)
            return None
    return data if isinstance(data, dict) else None


def find_leads_by_contact_id(db: Session, contact_id: int) -> list[dict[str, Any]]:
    """Все сделки клиента (для команды /status). Без фильтра по статусу — пусть UI решает.

    amoCRM не даёт фильтр /api/v4/leads по contact_id (filter[contacts][] и
    filter[contacts_id][] для leads-эндпоинта молча игнорируются и возвращают
    «топ» лидов аккаунта). Документированный путь — /contacts/{id}?with=leads
    выдаёт связанные lead-id, далее берём детали батчами через filter[id][].
    """
    cid = int(contact_id or 0)
    if cid <= 0:
        return []

    contact_url = f"{_base_url()}/api/v4/contacts/{cid}?with=leads"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(contact_url, headers=_headers(db))
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()

    linked = ((data or {}).get("_embedded") or {}).get("leads") or []
    lead_ids: list[int] = []
    for it in linked:
        if isinstance(it, dict):
            lid = it.get("id")
            if isinstance(lid, int):
                lead_ids.append(lid)
    if not lead_ids:
        return []

    leads: list[dict[str, Any]] = []
    chunk = 50
    for i in range(0, len(lead_ids), chunk):
        params: list[tuple[str, Any]] = []
        for lid in lead_ids[i : i + chunk]:
            params.append(("filter[id][]", lid))
        leads.extend(_fetch_leads(db, params))
    return leads


def extract_lead_field_value(lead: dict[str, Any], field_id: int) -> str:
    if int(field_id or 0) <= 0:
        return ""
    for grp in lead.get("custom_fields_values") or []:
        if grp.get("field_id") != int(field_id):
            continue
        for v in grp.get("values") or []:
            val = v.get("value")
            if val is None:
                continue
            return str(val).strip()
    return ""


def patch_lead_liveinform(db: Session, lead_id: int, status_line: str) -> None:
    s = get_settings()
    fid = int(s.amo_field_lead_liveinform_status or 0)
    if fid <= 0:
        log.warning("AMO_FIELD_LEAD_LIVEINFORM_STATUS не задан, пропуск PATCH лида %s", lead_id)
        return
    url = f"{_base_url()}/api/v4/leads"
    body = [{"id": int(lead_id), "custom_fields_values": [_build_cf_patch(fid, status_line)]}]
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(url, headers=_headers(db), json=body)
        if r.status_code >= 400:
            log.warning("patch_lead_liveinform %s: %s %s", lead_id, r.status_code, r.text[:800])


def find_catalog_elements_with_cdek(db: Session, tracking_raw: str) -> list[dict[str, Any]]:
    s = get_settings()
    if s.amo_order_entity.lower().strip() != "catalog":
        return []
    cat_id = int(s.amo_catalog_id or 0)
    cdek_f = int(s.amo_field_catalog_cdek or 0)
    if cat_id <= 0 or cdek_f <= 0:
        return []
    want_norm = normalize_tracking(tracking_raw)
    if not want_norm:
        return []

    url = f"{_base_url()}/api/v4/catalog_elements"
    base_params: list[tuple[str, Any]] = [
        ("limit", "250"),
        ("filter[catalog_id][]", cat_id),
    ]
    variants = [tracking_raw, tracking_raw.strip(), want_norm]
    merged: dict[int, dict[str, Any]] = {}

    for term in variants:
        if not str(term):
            continue
        params = [*base_params, ("query", str(term))]
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=_headers(db), params=params)
            if r.status_code >= 400:
                log.warning("catalog_elements query failed: %s %s", r.status_code, r.text[:600])
                continue
            data = r.json()
        emb = (data or {}).get("_embedded") or {}
        els = emb.get("catalog_elements") or emb.get("elements") or []
        if not isinstance(els, list):
            continue
        for el in els:
            if not isinstance(el, dict):
                continue
            eid = el.get("id")
            if isinstance(eid, int) and _entity_cdek_matches(el, cdek_f, want_norm):
                merged[eid] = el

    return list(merged.values())


def patch_catalog_liveinform(db: Session, catalog_id: int, element_id: int, status_line: str) -> None:
    s = get_settings()
    fid = int(s.amo_field_catalog_liveinform_status or 0)
    if fid <= 0:
        return
    url = f"{_base_url()}/api/v4/catalog_elements"
    body = [
        {
            "id": int(element_id),
            "catalog_id": int(catalog_id),
            "custom_fields_values": [_build_cf_patch(fid, status_line)],
        },
    ]
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(url, headers=_headers(db), json=body)
        if r.status_code >= 400:
            log.warning(
                "patch_catalog_liveinform elem %s: %s %s", element_id, r.status_code, r.text[:800]
            )


def filter_open_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить только открытые сделки (без closed_at и не в финальных статусах).

    Стандартные финальные статусы amoCRM: 142 = Успешно реализовано, 143 = Закрыто.
    """
    _FINAL_STATUS_IDS = {142, 143}
    result = []
    for lead in leads:
        if lead.get("closed_at") is not None:
            continue
        if lead.get("status_id") in _FINAL_STATUS_IDS:
            continue
        result.append(lead)
    return result


def get_delivery_statuses_from_amo(
    db: Session, amo_contact_id: int
) -> list[tuple[str, str]]:
    """Читает сделки контакта за текущий год и возвращает список (трек, статус).

    Статус берётся из поля Статус LiveInform. Если поле пустое — пустая строка.
    Сделки без трек-номера пропускаются.
    """
    import datetime as _dt

    s = get_settings()
    cdek_fid = int(s.amo_field_lead_cdek or 0)
    st_fid = int(s.amo_field_lead_liveinform_status or 0)

    all_leads = find_leads_by_contact_id(db, amo_contact_id)

    # Фильтр по году: берём сделки, созданные в текущем году
    current_year = _dt.datetime.now(_dt.timezone.utc).year
    year_start_ts = int(
        _dt.datetime(current_year, 1, 1, tzinfo=_dt.timezone.utc).timestamp()
    )
    year_leads = [
        lead for lead in all_leads
        if (lead.get("created_at") or 0) >= year_start_ts
    ]

    items: list[tuple[str, str]] = []
    for lead in year_leads:
        track = extract_lead_field_value(lead, cdek_fid) if cdek_fid else ""
        if not track:
            continue
        status = extract_lead_field_value(lead, st_fid) if st_fid else ""
        items.append((track.strip(), status.strip()))
    return items


def set_contact_bot_active(db: Session, contact_id: int, channel: str, active: bool) -> None:
    """Проставить/снять контактный чекбокс «Подписан на бот» для нужного канала.

    Telegram → amo_field_contact_bot_active, MAX → amo_field_contact_max_active.
    Используется при блокировке бота клиентом, чтобы аудитория в amo была реальной.
    """
    s = get_settings()
    ch = (channel or "telegram").strip().lower()
    fid = int((s.amo_field_contact_max_active if ch == "max" else s.amo_field_contact_bot_active) or 0)
    if fid <= 0 or int(contact_id or 0) <= 0:
        return
    try:
        patch_contact_fields(db, int(contact_id), {fid: bool(active)})
        log.info("set_contact_bot_active contact=%s channel=%s active=%s", contact_id, ch, active)
    except Exception:
        log.warning("set_contact_bot_active failed contact=%s", contact_id, exc_info=True)


def contact_field_is_true(db: Session, contact_id: int, field_id: int) -> bool:
    """Прочитать булево значение чекбокса контакта (например «Действующий»)."""
    fid = int(field_id or 0)
    cid = int(contact_id or 0)
    if fid <= 0 or cid <= 0:
        return False
    url = f"{_base_url()}/api/v4/contacts/{cid}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(db))
        if r.status_code >= 400:
            return False
        try:
            data = r.json()
        except Exception:
            return False
    for grp in data.get("custom_fields_values") or []:
        if grp.get("field_id") != fid:
            continue
        for v in grp.get("values") or []:
            val = v.get("value")
            if val in (True, "true", "1", 1):
                return True
    return False


def add_contact_note(db: Session, contact_id: int, text: str) -> bool:
    """Добавить текстовое примечание в карточку контакта."""
    cid = int(contact_id or 0)
    if cid <= 0 or not text.strip():
        return False
    url = f"{_base_url()}/api/v4/contacts/{cid}/notes"
    body = [{"note_type": "common", "params": {"text": text[:2000]}}]
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=_headers(db), json=body)
        if r.status_code >= 400:
            log.warning("add_contact_note %s: %s %s", cid, r.status_code, r.text[:300])
            return False
    return True


def create_lead_on_stage(
    db: Session,
    *,
    pipeline_id: int,
    status_id: int,
    contact_id: int,
    name: Optional[str] = None,
) -> Optional[int]:
    """Создать сделку в указанной воронке/этапе и привязать контакт."""
    if int(pipeline_id) <= 0 or int(status_id) <= 0 or int(contact_id) <= 0:
        log.warning("create_lead_on_stage: некорректные id pipe=%s status=%s contact=%s",
                    pipeline_id, status_id, contact_id)
        return None
    url = f"{_base_url()}/api/v4/leads"
    item: dict[str, Any] = {
        "pipeline_id": int(pipeline_id),
        "status_id": int(status_id),
        "_embedded": {"contacts": [{"id": int(contact_id), "is_main": True}]},
    }
    if name:
        item["name"] = name
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=_headers(db), json=[item])
        if r.status_code >= 400:
            log.warning("create_lead_on_stage: %s %s", r.status_code, r.text[:500])
            return None
        try:
            data = r.json()
        except Exception:
            return None
    leads = ((data or {}).get("_embedded") or {}).get("leads") or []
    if leads and isinstance(leads[0].get("id"), int):
        return int(leads[0]["id"])
    return None


def fetch_won_leads_closed(db: Session, pipeline_id: int, status_id: int,
                           from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    """Выигранные сделки воронки, закрытые в окне [from_ts, to_ts]. С пагинацией."""
    url = f"{_base_url()}/api/v4/leads"
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        params: list[tuple[str, Any]] = [
            ("filter[statuses][0][pipeline_id]", int(pipeline_id)),
            ("filter[statuses][0][status_id]", int(status_id)),
            ("filter[closed_at][from]", int(from_ts)),
            ("filter[closed_at][to]", int(to_ts)),
            ("with", "contacts"),
            ("limit", "250"),
            ("page", str(page)),
        ]
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=_headers(db), params=params)
            if r.status_code == 204:
                break
            if r.status_code >= 400:
                log.warning("fetch_won_leads_closed p%s: %s %s", page, r.status_code, r.text[:300])
                break
            try:
                data = r.json()
            except Exception:
                break
        leads = ((data or {}).get("_embedded") or {}).get("leads") or []
        out.extend(leads)
        if len(leads) < 250:
            break
        page += 1
    return out


def has_open_deal_excluding(db: Session, contact_id: int, exclude_status_ids: set[int]) -> bool:
    """Есть ли у контакта открытая сделка (кроме перечисленных этапов)."""
    leads = find_leads_by_contact_id(db, contact_id)
    for lead in filter_open_leads(leads):
        if lead.get("status_id") not in exclude_status_ids:
            return True
    return False


def has_lead_on_status(db: Session, contact_id: int, status_id: int) -> bool:
    """Есть ли у контакта сделка на конкретном этапе (для антидублей «Требует касания»)."""
    sid = int(status_id or 0)
    if sid <= 0:
        return False
    for lead in find_leads_by_contact_id(db, contact_id):
        if lead.get("status_id") == sid:
            return True
    return False


def collect_contact_ids_from_leads(leads: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for lead in leads:
        embedded = lead.get("_embedded") or {}
        contacts = embedded.get("contacts") or []
        if not isinstance(contacts, list):
            continue
        for c in contacts:
            if isinstance(c, dict):
                cid = c.get("id")
                if isinstance(cid, int) and cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
    return ids
