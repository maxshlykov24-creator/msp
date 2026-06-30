"""
Миграция enter (оприходований) из old в new, которые не попали в основную миграцию.
Переносит документы с позициями и всеми полями.

Запуск:
    python3 migrate_enter.py --dry    # только покажет что будет делать
    python3 migrate_enter.py          # реальный прогон
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
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REQUEST_SLEEP = 0.07

# Диапазон: декабрь 2025 (начальные остатки, не вошедшие в основную миграцию)
DATE_FROM = "2025-12-01 00:00:00"
DATE_TO   = "2025-12-31 23:59:59"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "migrate_enter.log", encoding="utf-8"),
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


def map_position(p, umap):
    a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
    old_a = uid(a_href)
    for etype in ("product", "variant", "service"):
        new_uuid = umap[etype].get(old_a)
        if new_uuid:
            np = {
                "assortment": meta_obj(etype, new_uuid),
                "quantity": p.get("quantity", 1),
                "price": p.get("price", 0),
            }
            if "costPrice" in p:
                np["costPrice"] = p["costPrice"]
            uom_href = (p.get("uom") or {}).get("meta", {}).get("href", "")
            uom_new = umap["uom"].get(uid(uom_href))
            if uom_new:
                np["uom"] = meta_obj("uom", uom_new)
            return np
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    enter_map = umap.get("enter", {})

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    log.info(f"Загружаю old enter {DATE_FROM} .. {DATE_TO}")
    old_enters = get_all(old_s, "entity/enter",
                         {"filter": f"moment>={DATE_FROM};moment<={DATE_TO}"})
    log.info(f"  old enter: {len(old_enters)}")

    # Загрузить уже существующие в new (по externalCode)
    log.info("Загружаю new enter (все)...")
    new_enters = get_all(new_s, "entity/enter", {})
    new_by_extcode = {}
    for e in new_enters:
        ec = e.get("externalCode", "")
        if ec:
            new_by_extcode[ec] = uid(e["meta"]["href"])
    log.info(f"  new enter: {len(new_enters)}")

    created = skipped = errors = 0

    for old in old_enters:
        old_uid_v = uid(old["meta"]["href"])

        # Уже смаппирован?
        if old_uid_v in enter_map:
            log.info(f"  уже смаппирован: {old.get('name','?')}, пропуск")
            skipped += 1
            continue

        # Уже есть в new по externalCode?
        ec = old.get("externalCode", old_uid_v)
        if ec in new_by_extcode:
            new_uid_v = new_by_extcode[ec]
            log.info(f"  уже есть в new по externalCode: {old.get('name','?')} -> {new_uid_v}")
            umap["enter"][old_uid_v] = new_uid_v
            skipped += 1
            continue

        # Загрузить полный old enter с позициями
        r = api_req(old_s, "GET", f"{BASE}/entity/enter/{old_uid_v}",
                    params={"expand": "positions"})
        if not r.ok:
            log.warning(f"  ✗ GET old enter/{old_uid_v}: {r.status_code}")
            errors += 1
            continue

        full = r.json()
        pos_data = full.get("positions", {})
        old_pos = pos_data.get("rows", []) if isinstance(pos_data, dict) else (pos_data or [])

        # Смапить позиции
        mapped = []
        unmapped = 0
        for p in old_pos:
            mp = map_position(p, umap)
            if mp is None:
                unmapped += 1
                log.warning(f"  ! enter/{old.get('name','?')}: позиция не смаплена "
                            f"({uid((p.get('assortment') or {}).get('meta',{}).get('href',''))})")
            else:
                mapped.append(mp)

        if not mapped and old_pos:
            log.warning(f"  ✗ enter/{old.get('name','?')}: 0 позиций смаппилось, пропуск")
            errors += 1
            continue

        # Смапить organization и store
        org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
        store_href = (full.get("store") or {}).get("meta", {}).get("href", "")
        new_org = umap["organization"].get(uid(org_href))
        new_store = umap["store"].get(uid(store_href))

        if not new_org:
            log.warning(f"  ✗ enter/{old.get('name','?')}: organization не смаппирована")
            errors += 1
            continue

        body = {
            "organization": meta_obj("organization", new_org),
            "moment": full.get("moment"),
            "description": full.get("description", ""),
            "externalCode": old_uid_v,
            "positions": mapped,
        }
        if new_store:
            body["store"] = meta_obj("store", new_store)

        project_href = (full.get("project") or {}).get("meta", {}).get("href", "")
        new_project = umap.get("project", {}).get(uid(project_href))
        if new_project:
            body["project"] = meta_obj("project", new_project)

        if dry := args.dry:
            log.info(f"  [DRY] POST enter/{old.get('name','?')}: {len(mapped)} поз, sum={old.get('sum',0)/100:.0f}р")
            created += 1
            continue

        r2 = api_req(new_s, "POST", f"{BASE}/entity/enter", json=body)
        if r2.ok:
            new_uid_v = uid(r2.json()["meta"]["href"])
            umap["enter"][old_uid_v] = new_uid_v
            created += 1
            log.info(f"  ✓ enter/{old.get('name','?')} -> {new_uid_v} (sum={r2.json().get('sum',0)/100:.0f}р vs old={old.get('sum',0)/100:.0f}р)")
        else:
            log.warning(f"  ✗ POST enter/{old.get('name','?')}: {r2.status_code} {r2.text[:300]}")
            errors += 1

    log.info(f"\nИТОГ: created={created} skipped={skipped} errors={errors}")

    # Сохранить обновлённый маппинг
    if not args.dry:
        MAP_FILE.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("uuid_map.json обновлён")


if __name__ == "__main__":
    main()
