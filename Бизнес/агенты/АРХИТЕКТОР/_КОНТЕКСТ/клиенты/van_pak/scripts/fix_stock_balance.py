"""
ШАГ 5: Пересоздать/обновить ввод остатков (enter) на 01.01.2026 со стоимостью.

Проблема: в new аккаунте введённые остатки могут иметь нулевую или неверную стоимость,
что влияет на себестоимость в Технологических операциях, Списаниях и Перемещениях.

Логика:
  1. Найти все enter (оприходование) в old, дата которых 2025-12-31 или 2026-01-01
     (они являются вводом начальных остатков).
  2. Для каждого old enter найти new enter через uuid_map["enter"].
  3. Загрузить позиции old enter (с costPrice/price).
  4. Сравнить с позициями new enter.
  5. Если есть расхождения — PUT new enter с позициями из old (смапив ассортимент).

Запуск:
    python3 fix_stock_balance.py --dry
    python3 fix_stock_balance.py
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

# Ввод остатков — дата на начало или конец декабря 2025
ENTER_DATE_FROM = "2025-12-01 00:00:00"
ENTER_DATE_TO   = "2026-01-31 23:59:59"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_stock_balance.log", encoding="utf-8"),
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
            # costPrice — ключевое поле себестоимости
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
    inv_enter = {v: k for k, v in enter_map.items()}  # new -> old

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    log.info("Загружаю enter из old...")
    old_enters = get_all(old_s, "entity/enter",
                         {"filter": f"moment>={ENTER_DATE_FROM};moment<={ENTER_DATE_TO}"})
    log.info(f"  old enter: {len(old_enters)}")

    log.info("Загружаю enter из new...")
    new_enters = get_all(new_s, "entity/enter",
                         {"filter": f"moment>={ENTER_DATE_FROM};moment<={ENTER_DATE_TO}"})
    log.info(f"  new enter: {len(new_enters)}")
    new_by_id = {uid(e["meta"]["href"]): e for e in new_enters}

    fixed = already_ok = no_map = skipped = errors = 0
    put_count = 0

    for old in old_enters:
        old_uid_v = uid(old["meta"]["href"])
        new_uid_v = enter_map.get(old_uid_v)
        if not new_uid_v:
            no_map += 1
            log.warning(f"  ! enter/{old.get('name','?')}: не смаппирован, пропуск")
            continue

        n = new_by_id.get(new_uid_v)
        if not n:
            no_map += 1
            continue

        old_sum = old.get("sum", 0) or 0
        new_sum = n.get("sum", 0) or 0
        if abs(old_sum - new_sum) <= 100:
            already_ok += 1
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

        if not old_pos:
            log.warning(f"  ! enter/{old.get('name','?')}: 0 позиций в old")
            skipped += 1
            continue

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

        if not mapped:
            skipped += 1
            continue

        if dry:
            log.info(f"  [DRY] enter/{old.get('name','?')}: {len(mapped)} поз, sum {new_sum/100:.2f}->{old_sum/100:.2f}")
            fixed += 1
            continue

        body = {"positions": mapped}
        r2 = api_req(new_s, "PUT", f"{BASE}/entity/enter/{new_uid_v}", json=body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... fixed={fixed}")
            time.sleep(62)

        if r2.ok:
            fixed += 1
            new_s2 = r2.json().get("sum", 0) or 0
            if abs(old_sum - new_s2) > 100:
                log.warning(f"  ! enter/{old.get('name','?')}: после PUT sum={new_s2/100:.2f} vs old={old_sum/100:.2f}")
        else:
            log.warning(f"  ✗ PUT enter/{new_uid_v}: {r2.status_code} {r2.text[:200]}")
            errors += 1

    log.info(f"\nИТОГ enter: fixed={fixed} already_ok={already_ok} no_map={no_map} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    main()
