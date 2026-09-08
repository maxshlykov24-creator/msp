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

_fields_cache: dict[str, Any] = {"at": 0.0, "map": {}}
_FIELDS_TTL_SEC = 3600


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def field_id_by_key(*, access_token: str, settings: Optional[Settings] = None) -> dict[str, int]:
    """Карта «ключ метки → id поля сделки». Кэш на час."""
    now = time.time()
    if _fields_cache["map"] and now - float(_fields_cache["at"]) < _FIELDS_TTL_SEC:
        return dict(_fields_cache["map"])

    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/custom_fields"
    out: dict[str, int] = {}
    by_code: dict[str, int] = {}
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
            page += 1

    for key, code in FIELD_CODE_BY_KEY.items():
        if code in by_code:
            out[key] = by_code[code]

    _fields_cache["map"] = dict(out)
    _fields_cache["at"] = now
    return out


def get_lead(*, lead_id: int, access_token: str, settings: Optional[Settings] = None) -> dict[str, Any]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/leads/{lead_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params={"with": "contacts"}, headers=_headers(access_token))
        r.raise_for_status()
        return r.json() or {}


def get_contact_phones(
    *, contact_id: int, access_token: str, settings: Optional[Settings] = None
) -> list[str]:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/contacts/{contact_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers(access_token))
        if r.status_code >= 400:
            return []
        doc = r.json() or {}
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


def subscribe_webhook(
    *,
    destination: str,
    events: list[str],
    access_token: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/webhooks"
    body = [{"destination": destination, "settings": events}]
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, json=body, headers=_headers(access_token))
