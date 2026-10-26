from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.oauth_amocrm import api_base

log = logging.getLogger(__name__)

# code поля в amoCRM → ключ метки в нашем tracking-словаре.
# Коды стандартные для полей типа tracking_data, поэтому переносятся на других клиентов без правок.
FIELD_CODE_BY_KEY = {
    "utm_source": "UTM_SOURCE",
    "utm_medium": "UTM_MEDIUM",
    "utm_campaign": "UTM_CAMPAIGN",
    "utm_term": "UTM_TERM",
    "utm_content": "UTM_CONTENT",
    "utm_referrer": "UTM_REFERRER",
    "roistat": "ROISTAT",
    "yclid": "YCLID",
}

_fields_cache: dict[str, Any] = {"at": 0.0, "map": {}, "selects": {}}
_FIELDS_TTL_SEC = 3600


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _refresh_fields(*, access_token: str, settings: Optional[Settings] = None) -> None:
    now = time.time()
    if _fields_cache["at"] and now - float(_fields_cache["at"]) < _FIELDS_TTL_SEC:
        return
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/custom_fields"
    by_code: dict[str, int] = {}
    selects: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=30.0) as client:
        page = 1
        while page <= 10:
            r = client.get(url, params={"limit": 250, "page": page}, headers=_headers(access_token))
            if r.status_code == 204:
                break
            r.raise_for_status()
            items = ((r.json() or {}).get("_embedded") or {}).get("custom_fields") or []
            if not items:
                break
            for f in items:
                code = (f.get("code") or "").upper()
                if code:
                    by_code[code] = int(f["id"])
                if (f.get("type") or "") in {"select", "multiselect", "radiobutton"}:
                    enums: dict[str, int] = {}
                    for enum in f.get("enums") or []:
                        val = enum.get("value")
                        if val not in (None, "") and enum.get("id") is not None:
                            enums[str(val)] = int(enum["id"])
                    name = (f.get("name") or "").strip()
                    if name and name not in selects:
                        selects[name] = {"field_id": int(f["id"]), "enums": enums}
            page += 1
    out: dict[str, int] = {}
    for key, code in FIELD_CODE_BY_KEY.items():
        if code in by_code:
            out[key] = by_code[code]
    _fields_cache["map"] = dict(out)
    _fields_cache["selects"] = selects
    _fields_cache["at"] = now


def field_id_by_key(*, access_token: str, settings: Optional[Settings] = None) -> dict[str, int]:
    """Карта «ключ метки → id поля сделки». Кэш на час."""
    _refresh_fields(access_token=access_token, settings=settings)
    return dict(_fields_cache["map"])


def select_field(
    *, name: str, access_token: str, settings: Optional[Settings] = None
) -> dict[str, Any]:
    """Поле-список сделки по имени: field_id и enum_id значений."""
    _refresh_fields(access_token=access_token, settings=settings)
    found = (_fields_cache.get("selects") or {}).get(name) or {}
    return {"field_id": found.get("field_id"), "enums": dict(found.get("enums") or {})}


def get_lead(*, lead_id: int, access_token: str, settings: Optional[Settings] = None) -> dict[str, Any]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/{lead_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params={"with": "contacts"}, headers=_headers(access_token))
        r.raise_for_status()
        return r.json() or {}


def get_contact(
    *, contact_id: int, access_token: str, settings: Optional[Settings] = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/contacts/{contact_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(access_token))
        if r.status_code >= 400:
            return {}
        return r.json() or {}


def get_contact_phones(
    *, contact_id: int, access_token: str, settings: Optional[Settings] = None
) -> list[str]:
    doc = get_contact(contact_id=contact_id, access_token=access_token, settings=settings)
    phones: list[str] = []
    for cf in doc.get("custom_fields_values") or []:
        if (cf.get("field_code") or "").upper() != "PHONE":
            continue
        for v in cf.get("values") or []:
            val = v.get("value")
            if val:
                phones.append(str(val))
    return phones


def lead_phones(*, lead: dict[str, Any], access_token: str, settings: Optional[Settings] = None) -> list[str]:
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    phones: list[str] = []
    for c in contacts:
        cid = c.get("id")
        if cid:
            phones.extend(get_contact_phones(contact_id=int(cid), access_token=access_token, settings=settings))
    return phones


def lead_contact_names(
    *, lead: dict[str, Any], access_token: str, settings: Optional[Settings] = None
) -> list[str]:
    """Имена контактов сделки — у чата без телефона это имя посетителя Talk-me."""
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    names: list[str] = []
    for c in contacts:
        cid = c.get("id")
        if not cid:
            continue
        doc = get_contact(contact_id=int(cid), access_token=access_token, settings=settings)
        name = (doc.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def lead_field_filled(lead: dict[str, Any], field_id: int) -> bool:
    """В поле уже есть значение или выбран пункт списка."""
    for cf in lead.get("custom_fields_values") or []:
        if cf.get("field_id") != field_id:
            continue
        for v in cf.get("values") or []:
            if v.get("enum_id") not in (None, "") or v.get("value") not in (None, ""):
                return True
    return False


def existing_tracking_values(*, lead: dict[str, Any], id_by_key: dict[str, int]) -> dict[str, str]:
    """Какие метки в сделке уже заполнены — их не трогаем."""
    key_by_id = {v: k for k, v in id_by_key.items()}
    out: dict[str, str] = {}
    for cf in lead.get("custom_fields_values") or []:
        key = key_by_id.get(cf.get("field_id"))
        if not key:
            continue
        for v in cf.get("values") or []:
            val = v.get("value")
            if val not in (None, ""):
                out[key] = str(val)
                break
    return out


def build_custom_fields_payload(
    *, tracking: dict[str, Any], id_by_key: dict[str, int], skip_keys: set[str]
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for key, field_id in id_by_key.items():
        if key in skip_keys:
            continue
        val = tracking.get(key)
        if val in (None, ""):
            continue
        values.append({"field_id": field_id, "values": [{"value": str(val)}]})
    return values


def patch_lead_fields(
    *,
    lead_id: int,
    custom_fields_values: list[dict[str, Any]],
    access_token: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/{lead_id}"
    body = {"custom_fields_values": custom_fields_values}
    with httpx.Client(timeout=30.0) as client:
        return client.patch(url, json=body, headers=_headers(access_token))


def list_webhooks(*, access_token: str, settings: Optional[Settings] = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/webhooks"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(access_token))
        if r.status_code == 204:
            return []
        r.raise_for_status()
        return ((r.json() or {}).get("_embedded") or {}).get("webhooks") or []


def format_phone_e164(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}" if digits else ""


def find_contact_by_phone(
    *, phone: str, access_token: str, settings: Optional[Settings] = None
) -> Optional[dict[str, Any]]:
    """Первый контакт, у которого совпадают последние 10 цифр телефона."""
    from app.talkme_parse import normalize_phone

    settings = settings or get_settings()
    key = normalize_phone(phone)
    if not key:
        return None
    url = f"{api_base(settings)}/api/v4/contacts"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            url,
            params={"query": key, "limit": 10},
            headers=_headers(access_token),
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        contacts = ((r.json() or {}).get("_embedded") or {}).get("contacts") or []
    for c in contacts:
        cid = c.get("id")
        if not cid:
            continue
        phones = get_contact_phones(contact_id=int(cid), access_token=access_token, settings=settings)
        if any(normalize_phone(p) == key for p in phones):
            return c
    # Search can return a partial match. Never bind a call to another person.
    return None


def create_contact(
    *,
    name: str,
    phone: str,
    email: Optional[str] = None,
    access_token: str,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    fields: list[dict[str, Any]] = [
        {
            "field_code": "PHONE",
            "values": [{"value": format_phone_e164(phone), "enum_code": "WORK"}],
        }
    ]
    if email:
        fields.append(
            {"field_code": "EMAIL", "values": [{"value": email, "enum_code": "WORK"}]}
        )
    body = [{"name": name or format_phone_e164(phone), "custom_fields_values": fields}]
    url = f"{api_base(settings)}/api/v4/contacts"
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=body, headers=_headers(access_token))
        r.raise_for_status()
        created = ((r.json() or {}).get("_embedded") or {}).get("contacts") or []
    if not created:
        raise RuntimeError("amoCRM: контакт не создался")
    return created[0]


def create_lead(
    *,
    name: str,
    pipeline_id: int,
    status_id: int,
    contact_id: int,
    custom_fields_values: Optional[list[dict[str, Any]]] = None,
    tags: Optional[list[str]] = None,
    access_token: str,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    body: dict[str, Any] = {
        "name": name,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "_embedded": {"contacts": [{"id": contact_id}]},
    }
    if custom_fields_values:
        body["custom_fields_values"] = custom_fields_values
    if tags:
        body["_embedded"]["tags"] = [{"name": t} for t in tags if t]
    url = f"{api_base(settings)}/api/v4/leads"
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=[body], headers=_headers(access_token))
        r.raise_for_status()
        created = ((r.json() or {}).get("_embedded") or {}).get("leads") or []
    if not created:
        raise RuntimeError("amoCRM: сделка не создалась")
    return created[0]


def add_lead_note(
    *,
    lead_id: int,
    text: str,
    access_token: str,
    settings: Optional[Settings] = None,
    raise_on_error: bool = False,
) -> None:
    if not (text or "").strip():
        return
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/{lead_id}/notes"
    body = [{"note_type": "common", "params": {"text": text.strip()}}]
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=body, headers=_headers(access_token))
        if r.status_code >= 400:
            log.error("note lead %s failed: %s %s", lead_id, r.status_code, r.text[:300])
            if raise_on_error:
                r.raise_for_status()


def search_leads(
    *,
    query: str,
    access_token: str,
    settings: Optional[Settings] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads"
    with httpx.Client(timeout=20.0) as client:
        r = client.get(
            url,
            params={"query": query, "limit": limit, "order[created_at]": "desc"},
            headers=_headers(access_token),
        )
        if r.status_code == 204 or not r.content:
            return []
        r.raise_for_status()
        return ((r.json() or {}).get("_embedded") or {}).get("leads") or []


def lead_has_note_text(
    *,
    lead_id: int,
    needle: str,
    access_token: str,
    settings: Optional[Settings] = None,
) -> bool:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/{lead_id}/notes"
    with httpx.Client(timeout=20.0) as client:
        for page in range(1, 101):
            r = client.get(url, params={"limit": 250, "page": page}, headers=_headers(access_token))
            r.raise_for_status()
            if r.status_code == 204 or not r.content:
                return False
            doc = r.json() or {}
            notes = (doc.get("_embedded") or {}).get("notes") or []
            if any(needle in str((n.get("params") or {}).get("text") or "") for n in notes):
                return True
            if not notes or not (doc.get("_links") or {}).get("next"):
                return False
    raise RuntimeError("note_scan_limit")


def _phone_leads(
    *, phone: str, access_token: str, settings: Optional[Settings] = None
) -> list[dict[str, Any]]:
    """Exact-phone candidates; API errors must be retried, not treated as absence."""
    settings = settings or get_settings()
    contact = find_contact_by_phone(phone=phone, access_token=access_token, settings=settings)
    if not contact or not contact.get("id"):
        return []
    url = f"{api_base(settings)}/api/v4/contacts/{int(contact['id'])}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params={"with": "leads"}, headers=_headers(access_token))
        r.raise_for_status()
        if r.status_code == 204:
            return []
        leads = ((r.json() or {}).get("_embedded") or {}).get("leads") or []
    ids = sorted((int(l["id"]) for l in leads if l.get("id")), reverse=True)
    found: list[dict[str, Any]] = []
    for lead_id in ids[:15]:
        found.append(get_lead(lead_id=lead_id, access_token=access_token, settings=settings))
    return found


def open_leads_for_phone(
    *, phone: str, access_token: str, settings: Optional[Settings] = None
) -> list[dict[str, Any]]:
    return [lead for lead in _phone_leads(phone=phone, access_token=access_token, settings=settings)
            if int(lead.get("status_id") or 0) not in (142, 143)]


def recent_closed_leads_for_call(*, phone: str, started_at, access_token: str,
                                settings: Optional[Settings] = None) -> list[dict[str, Any]]:
    """A short-lived deal may already be closed before the five-minute worker."""
    import datetime as dt
    settings = settings or get_settings()
    stamp = started_at.timestamp()
    return [lead for lead in _phone_leads(phone=phone, access_token=access_token, settings=settings)
            if int(lead.get("status_id") or 0) in (142, 143)
            and stamp - settings.ct_call_window_minutes * 60 <= int(lead.get("created_at") or 0) <= stamp + 120
            and int(lead.get("closed_at") or 0) >= stamp - 120]


def subscribe_webhook(
    *,
    destination: str,
    events: list[str],
    access_token: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/webhooks"
    body = {"destination": destination, "settings": events}
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, json=body, headers=_headers(access_token))
