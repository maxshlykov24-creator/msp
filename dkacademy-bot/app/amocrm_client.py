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
            r.raise_for_status()
            data = r.json()
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
