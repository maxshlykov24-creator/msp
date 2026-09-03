"""Shared MoySklad helpers for Van Pak OLD↔NEW sync scripts."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

OLD_TOKEN = os.environ.get("OLD_TOKEN", "e95af8f2c93487a215f754ea1e5469e281613d61")
NEW_TOKEN = os.environ.get("NEW_TOKEN", "619a40e860cb6ad975c3d05cae7157b29caccc0e")
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REQUEST_SLEEP = 0.08

DOC_TYPES_WITH_AGENT = [
    "customerorder", "demand", "invoiceout", "invoicein", "supply",
    "paymentin", "paymentout", "purchaseorder", "purchasereturn",
    "factureout", "cashin", "cashout",
]

ORDER_ATTR_NAMES = [
    "Способ поставки товара",
    "Оплачен менеджеру",
    "Документ отгрузки подписан",
    "Уведомление отправлено",
]
# «Адрес доставки» в UI — поле customerorder.shipmentAddress (не в ORDER_ATTR_NAMES).

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


def api(s: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    for attempt in range(8):
        try:
            r = s.request(method, url, timeout=60, **kwargs)
        except requests.exceptions.RequestException:
            if attempt < 7:
                time.sleep(min(30, 2**attempt))
                continue
            raise
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            time.sleep(wait + 0.5)
            continue
        if r.status_code == 403:
            # 1061 = API access revoked; never retry — avoids auto-block escalation
            time.sleep(REQUEST_SLEEP)
            return r
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2**attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    return r


def load_umap() -> dict:
    return json.loads(MAP_FILE.read_text(encoding="utf-8"))


def save_umap(umap: dict) -> None:
    MAP_FILE.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")


def meta_obj(entity_type: str, uuid: str) -> dict:
    return {
        "meta": {
            "href": f"{BASE}/entity/{entity_type}/{uuid}",
            "type": entity_type,
            "mediaType": "application/json",
        }
    }


def get_all(
    s: requests.Session,
    entity: str,
    filt: str | None = None,
    expand: str | None = None,
    limit: int = 100,
) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if filt:
            params["filter"] = filt
        if expand:
            params["expand"] = expand
        r = api(s, "GET", f"{BASE}/entity/{entity}", params=params)
        if not r.ok:
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def org_uid(doc: dict) -> str:
    return uid((doc.get("organization") or {}).get("meta", {}).get("href", ""))


def agent_uid(doc: dict) -> str:
    return uid((doc.get("agent") or {}).get("meta", {}).get("href", ""))


def agent_type(doc: dict) -> str:
    href = (doc.get("agent") or {}).get("meta", {}).get("href", "")
    if "/entity/" in href:
        return href.split("/entity/")[-1].split("/")[0]
    return "counterparty"


def attr_val(doc: dict, name: str) -> Any:
    for a in doc.get("attributes") or []:
        if a.get("name") == name:
            v = a.get("value")
            if isinstance(v, dict):
                return v.get("name") or uid((v.get("meta") or {}).get("href", ""))
            return v
    return None


def old_api_available(s: requests.Session) -> bool:
    r = api(s, "GET", f"{BASE}/entity/organization", params={"limit": 1})
    return r.status_code == 200


def fetch_old_doc(
    s: requests.Session, doc_type: str, old_id: str,
) -> tuple[dict | None, str]:
    r = api(s, "GET", f"{BASE}/entity/{doc_type}/{old_id}")
    if r.status_code == 403:
        return None, "old_api_403"
    if r.ok:
        return r.json(), "ok"
    return None, f"old_http_{r.status_code}"


def is_uuid(s: str | None) -> bool:
    return bool(s and UUID_RE.match(s.strip()))


def mapped_agent(old_agent_uid: str, agent_t: str, umap: dict) -> str | None:
    if agent_t == "organization":
        return umap.get("organization", {}).get(old_agent_uid)
    return umap.get("counterparty", {}).get(old_agent_uid)


def map_order_attributes(old_attrs: list, umap: dict) -> list[dict]:
    """Map customerorder attributes from OLD list to NEW meta format."""
    doc_type = "customerorder"
    attr_map = umap.get(f"attr_{doc_type}", {})
    ce_val = umap.get("customentity_value", {})
    ce_dict = umap.get("customentity_dict", {})
    result = []
    for attr in old_attrs or []:
        name = attr.get("name")
        if name not in ORDER_ATTR_NAMES:
            continue
        old_attr_uuid = uid((attr.get("meta") or {}).get("href", ""))
        new_attr_uuid = attr_map.get(old_attr_uuid)
        if not new_attr_uuid:
            continue
        attr_type = attr.get("type")
        value = attr.get("value")
        if attr_type == "customentity" and isinstance(value, dict):
            val_href = (value.get("meta") or {}).get("href", "")
            old_val_uuid = uid(val_href)
            new_val_uuid = ce_val.get(old_val_uuid)
            parts = val_href.split("/")
            if not new_val_uuid or len(parts) < 2:
                continue
            old_dict_uuid = parts[-2]
            new_dict_uuid = ce_dict.get(old_dict_uuid)
            if not new_dict_uuid:
                continue
            value = {
                "meta": {
                    "href": f"{BASE}/entity/customentity/{new_dict_uuid}/{new_val_uuid}",
                    "type": "customentity",
                    "mediaType": "application/json",
                }
            }
        new_attr = {
            "meta": {
                "href": f"{BASE}/entity/{doc_type}/metadata/attributes/{new_attr_uuid}",
                "type": "attributemetadata",
                "mediaType": "application/json",
            },
            "value": value,
        }
        result.append(new_attr)
    return result


def moment_org_key(doc: dict, umap: dict, side: str) -> tuple[str, str] | None:
    """Return (new_org_uid, moment) for indexing; side=new uses doc org directly."""
    m = doc.get("moment") or ""
    ou = org_uid(doc)
    if not m or not ou:
        return None
    if side == "old":
        ou = umap.get("organization", {}).get(ou) or ou
    return (ou, m)


def classify_pair(
    old_doc: dict | None,
    new_doc: dict,
    old_id: str | None,
    umap: dict,
    doc_type: str = "",
) -> tuple[str, str]:
    """Return (status, reason)."""
    if not old_id:
        return "UNMAPPED_NEW", "no_old_id"
    new_id = uid(new_doc["meta"]["href"])
    if not old_doc:
        ext = (new_doc.get("externalCode") or "").strip()
        inv = {v: k for k, v in umap.get(doc_type, {}).items()}
        map_old_id = inv.get(new_id)
        ext_ok = is_uuid(ext) and ext == old_id
        map_ok = map_old_id == old_id if map_old_id else False
        if ext_ok and map_ok:
            return "TRUSTED", "anchor_only_old_unavailable"
        if ext_ok or map_ok:
            return "TRUSTED", "partial_anchor_old_unavailable"
        return "SUSPECT", "old_not_found"
    ext = (new_doc.get("externalCode") or "").strip()
    inv = {v: k for k, v in umap.get(doc_type, {}).items()}
    map_old_id = inv.get(new_id)

    ext_ok = is_uuid(ext) and ext == old_id
    map_ok = map_old_id == old_id if map_old_id else False
    if ext_ok and map_ok:
        pass
    elif ext_ok and not map_ok:
        return "SUSPECT", f"ext={ext[:8]} map={str(map_old_id)[:8] if map_old_id else None}"
    elif not ext_ok and map_ok:
        pass
    elif not ext_ok and not map_ok:
        return "SUSPECT", "ext_and_map_mismatch"

    if old_doc.get("moment") != new_doc.get("moment"):
        return "SUSPECT", "moment_mismatch"
    old_org = umap.get("organization", {}).get(org_uid(old_doc))
    if old_org and org_uid(new_doc) and old_org != org_uid(new_doc):
        return "SUSPECT", "org_mismatch"
    old_sum = old_doc.get("sum") or 0
    new_sum = new_doc.get("sum") or 0
    if old_sum and new_sum and old_sum != new_sum:
        return "SUSPECT", f"sum_{old_sum}_{new_sum}"

    if old_doc.get("name") != new_doc.get("name"):
        return "TRUSTED_RENAME", "name_diff"
    return "TRUSTED", "ok"


def resolve_moment_pairs(
    suspect_new: list[dict],
    old_docs: list[dict],
    new_docs: list[dict],
    umap: dict,
    paired_old: set[str],
    paired_new: set[str],
) -> dict[str, tuple[str, str]]:
    """new_id -> (old_id, reason) for unique moment+org matches."""
    resolutions: dict[str, tuple[str, str]] = {}
    old_by_key: dict[tuple[str, str], list[str]] = {}
    new_by_key: dict[tuple[str, str], list[str]] = {}

    for o in old_docs:
        oid = uid(o["meta"]["href"])
        if oid in paired_old:
            continue
        k = moment_org_key(o, umap, "old")
        if k:
            old_by_key.setdefault(k, []).append(oid)

    for n in new_docs:
        nid = uid(n["meta"]["href"])
        if nid in paired_new or nid not in {uid(x["meta"]["href"]) for x in suspect_new}:
            continue
        k = moment_org_key(n, umap, "new")
        if k:
            new_by_key.setdefault(k, []).append(nid)

    for k, nids in new_by_key.items():
        oids = old_by_key.get(k, [])
        if len(nids) == 1 and len(oids) == 1:
            resolutions[nids[0]] = (oids[0], "moment_org_1_1")
        elif len(nids) == 1 and len(oids) > 1:
            n_doc = next(x for x in new_docs if uid(x["meta"]["href"]) == nids[0])
            ns = n_doc.get("sum") or 0
            old_by_id = {uid(d["meta"]["href"]): d for d in old_docs}
            matches = [o for o in oids if (old_by_id.get(o) or {}).get("sum") == ns]
            if len(matches) == 1:
                resolutions[nids[0]] = (matches[0], "moment_org_sum_1_1")
    return resolutions
