"""
Catchup: перенести документы из old в new, которых ещё нет в uuid_map.
Типы: customerorder, invoiceout, demand, paymentin

Алгоритм:
  1. Загрузить все old документы 2026 года.
  2. Найти те, которых нет в uuid_map[doc_type].
  3. Создать их в new (POST) с позициями и базовыми полями.
  4. Сохранить UUID в uuid_map.

Запуск:
    python3 catchup_new_docs.py --dry
    python3 catchup_new_docs.py
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REQUEST_SLEEP = 0.08

DOC_TYPES = ["customerorder", "invoiceout", "demand", "paymentin"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "catchup_new_docs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api_req(s, method, url, **kwargs):
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=40, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url}: {e}") from e
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            log.warning(f"429 rate-limit, жду {wait:.1f}с")
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2 ** attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def get_all(s, path, params=None):
    p = dict(params or {})
    limit = 100 if p.get("expand") else 1000
    p["limit"] = limit
    rows, offset = [], 0
    while True:
        p["offset"] = offset
        r = api_req(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
            log.warning(f"GET {path}: {r.status_code} {r.text[:150]}")
            break
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def meta_obj(entity_type, uuid):
    return {"meta": {
        "href": f"{BASE}/entity/{entity_type}/{uuid}",
        "type": entity_type,
        "mediaType": "application/json",
    }}


def map_agent(href, umap):
    old_a = uid(href)
    return umap.get("counterparty", {}).get(old_a)


def map_org(href, umap):
    return umap.get("organization", {}).get(uid(href))


def map_store(href, umap):
    return umap.get("store", {}).get(uid(href))


def map_project(href, umap):
    return umap.get("project", {}).get(uid(href))


def map_contract(href, umap):
    return umap.get("contract", {}).get(uid(href))


def map_employee(href, umap):
    return umap.get("employee", {}).get(uid(href))


def map_position(p, umap):
    a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
    old_a = uid(a_href)
    a_type = (p.get("assortment") or {}).get("meta", {}).get("type", "product")
    new_uuid = (umap.get(a_type, {}).get(old_a)
                or umap.get("product", {}).get(old_a)
                or umap.get("variant", {}).get(old_a)
                or umap.get("service", {}).get(old_a))
    if not new_uuid:
        return None
    np = {
        "assortment": meta_obj(a_type if a_type in ("product", "variant", "service") else "product", new_uuid),
        "quantity": p.get("quantity", 1),
        "price": p.get("price", 0),
        "discount": p.get("discount", 0),
        "vat": p.get("vat", 0),
    }
    uom_href = (p.get("uom") or {}).get("meta", {}).get("href", "")
    uom_new = umap.get("uom", {}).get(uid(uom_href))
    if uom_new:
        np["uom"] = meta_obj("uom", uom_new)
    return np


def get_positions(s, doc_type, doc_uid):
    r = api_req(s, "GET", f"{BASE}/entity/{doc_type}/{doc_uid}/positions",
                params={"limit": 1000})
    if r.ok:
        return r.json().get("rows", [])
    return []


def build_body(old_full, doc_type, umap, old_s):
    old_uid = uid(old_full["meta"]["href"])
    body = {
        "externalCode": old_uid,
        "moment": old_full.get("moment"),
        "description": old_full.get("description", "") or "",
    }

    # organization
    org_href = (old_full.get("organization") or {}).get("meta", {}).get("href", "")
    new_org = map_org(org_href, umap)
    if new_org:
        body["organization"] = meta_obj("organization", new_org)

    # agent (counterparty)
    agent_href = (old_full.get("agent") or {}).get("meta", {}).get("href", "")
    new_agent = map_agent(agent_href, umap)
    if new_agent:
        body["agent"] = meta_obj("counterparty", new_agent)

    # store
    store_href = (old_full.get("store") or {}).get("meta", {}).get("href", "")
    new_store = map_store(store_href, umap)
    if new_store:
        body["store"] = meta_obj("store", new_store)

    # project
    proj_href = (old_full.get("project") or {}).get("meta", {}).get("href", "")
    new_proj = map_project(proj_href, umap)
    if new_proj:
        body["project"] = meta_obj("project", new_proj)

    # contract
    cont_href = (old_full.get("contract") or {}).get("meta", {}).get("href", "")
    new_cont = map_contract(cont_href, umap)
    if new_cont:
        body["contract"] = meta_obj("contract", new_cont)

    # state (статус документа)
    state_href = (old_full.get("state") or {}).get("meta", {}).get("href", "")
    state_key = f"state_{doc_type}"
    new_state = umap.get(state_key, {}).get(uid(state_href))
    if new_state:
        body["state"] = meta_obj("state", new_state)

    # Для demand — customerOrder
    if doc_type == "demand":
        co_href = (old_full.get("customerOrder") or {}).get("meta", {}).get("href", "")
        new_co = umap.get("customerorder", {}).get(uid(co_href))
        if new_co:
            body["customerOrder"] = meta_obj("customerorder", new_co)

    # Для paymentin — операции и номер
    if doc_type == "paymentin":
        body["incomingNumber"] = old_full.get("incomingNumber", "") or ""
        ops = old_full.get("operations") or []
        mapped_ops = []
        for op in ops:
            op_meta = (op.get("meta") or {})
            op_type = op_meta.get("type", "")
            old_op_uid = uid(op_meta.get("href", ""))
            new_op_uid = umap.get(op_type, {}).get(old_op_uid)
            if new_op_uid:
                entry = {"meta": {
                    "href": f"{BASE}/entity/{op_type}/{new_op_uid}",
                    "type": op_type,
                    "mediaType": "application/json",
                }}
                if "linkedSum" in op:
                    entry["linkedSum"] = op["linkedSum"]
                mapped_ops.append(entry)
        if mapped_ops:
            body["operations"] = mapped_ops
        # Сумма и входящий платёж
        body["sum"] = old_full.get("sum", 0)
        return body  # у paymentin нет positions

    # Позиции
    pos_rows = get_positions(old_s, doc_type, old_uid)
    mapped = []
    for p in pos_rows:
        mp = map_position(p, umap)
        if mp:
            mapped.append(mp)
    if mapped:
        body["positions"] = mapped

    return body


def catchup_type(doc_type, old_s, new_s, umap, dry):
    log.info(f"\n{'='*55}\n{doc_type}\n{'='*55}")
    dmap = umap.get(doc_type, {})

    old_rows = get_all(old_s, f"entity/{doc_type}",
                       {"filter": f"moment>={YEAR_START}", "expand": "operations" if doc_type == "paymentin" else ""})
    not_mapped = [d for d in old_rows if uid(d["meta"]["href"]) not in dmap]
    log.info(f"  old={len(old_rows)} не смаппировано={len(not_mapped)}")

    created = skipped = errors = 0
    for old in not_mapped:
        old_uid_v = uid(old["meta"]["href"])
        name = old.get("name", "?")

        # Загрузить полный документ
        if doc_type != "paymentin":
            r = api_req(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_uid_v}")
            if not r.ok:
                log.warning(f"  ✗ GET {doc_type}/{name}: {r.status_code}")
                errors += 1
                continue
            old_full = r.json()
        else:
            old_full = old

        body = build_body(old_full, doc_type, umap, old_s)

        if dry:
            log.info(f"  [DRY] POST {doc_type}/{name}: sum={old.get('sum',0)/100:.0f}р")
            created += 1
            continue

        r2 = api_req(new_s, "POST", f"{BASE}/entity/{doc_type}", json=body)
        if r2.ok:
            new_uid_v = uid(r2.json()["meta"]["href"])
            umap[doc_type][old_uid_v] = new_uid_v
            created += 1
            log.info(f"  ✓ {doc_type}/{name} -> {new_uid_v}")
        else:
            log.warning(f"  ✗ POST {doc_type}/{name}: {r2.status_code} {r2.text[:300]}")
            errors += 1

    log.info(f"  ИТОГ {doc_type}: created={created} skipped={skipped} errors={errors}")
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    types = [args.only] if args.only else DOC_TYPES
    total = 0
    for dt in types:
        total += catchup_type(dt, old_s, new_s, umap, args.dry)

    if not args.dry and total > 0:
        MAP_FILE.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"\nuuid_map.json обновлён (+{total} документов)")

    log.info(f"\nВсего создано: {total}")


if __name__ == "__main__":
    main()
