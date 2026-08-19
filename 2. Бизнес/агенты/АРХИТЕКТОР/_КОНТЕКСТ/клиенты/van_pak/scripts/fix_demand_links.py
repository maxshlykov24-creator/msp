"""
ШАГ 2: Проставить customerOrder во все demand 2026, где связь отсутствует.

Логика:
  1. Загрузить все demand 2026 из new аккаунта.
  2. Для каждого demand без customerOrder — найти соответствующий old demand
     через uuid_map["demand"][new_uuid] -> old_uuid.
  3. Прочитать old demand, взять old.customerOrder.meta.href -> old_co_uuid.
  4. Смапить через uuid_map["customerorder"][old_co_uuid] -> new_co_uuid.
  5. PUT demand с {"customerOrder": meta_obj("customerorder", new_co_uuid)}.

Запуск:
    python3 fix_demand_links.py --dry   # только статистика
    python3 fix_demand_links.py         # реальный прогон
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
REQUEST_SLEEP = 0.07

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_demand_links.log", encoding="utf-8"),
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    demand_map = umap.get("demand", {})           # old_uuid -> new_uuid
    co_map = umap.get("customerorder", {})        # old_uuid -> new_uuid
    # Обратный маппинг new_demand -> old_demand
    inv_demand = {v: k for k, v in demand_map.items()}

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    # Загрузка new demand 2026
    log.info("Загружаю demand new 2026...")
    new_demands = get_all(new_s, "entity/demand",
                          {"filter": f"moment>={YEAR_START}", "expand": "customerOrder"})
    log.info(f"  Всего new demand: {len(new_demands)}")

    need_fix = []
    already_ok = 0
    no_inv_map = 0

    for nd in new_demands:
        co = nd.get("customerOrder")
        if co and (co.get("meta") or {}).get("href"):
            already_ok += 1
            continue
        new_uid = uid(nd["meta"]["href"])
        old_uid = inv_demand.get(new_uid)
        if not old_uid:
            no_inv_map += 1
            continue
        need_fix.append((new_uid, old_uid, nd.get("name", "?")))

    log.info(f"  already_ok={already_ok} need_fix={len(need_fix)} no_inv_map={no_inv_map}")

    if not need_fix:
        log.info("Нечего чинить.")
        return

    if args.dry:
        log.info("[DRY] Прогон закончен, реальных изменений нет.")
        return

    fixed = 0
    skipped = 0
    errors = 0
    put_count = 0

    for new_uid, old_uid, name in need_fix:
        # Взять old demand
        r = api_req(old_s, "GET", f"{BASE}/entity/demand/{old_uid}",
                    params={"expand": "customerOrder"})
        if not r.ok:
            log.warning(f"  ✗ GET old demand/{old_uid}: {r.status_code}")
            errors += 1
            continue

        old_d = r.json()
        old_co = old_d.get("customerOrder")
        if not old_co or not (old_co.get("meta") or {}).get("href"):
            log.warning(f"  ! demand/{name}: в old нет customerOrder, пропуск")
            skipped += 1
            continue

        old_co_uid = uid(old_co["meta"]["href"])
        new_co_uid = co_map.get(old_co_uid)
        if not new_co_uid:
            log.warning(f"  ! demand/{name}: old_co={old_co_uid} не смаппирован")
            skipped += 1
            continue

        body = {"customerOrder": meta_obj("customerorder", new_co_uid)}
        r2 = api_req(new_s, "PUT", f"{BASE}/entity/demand/{new_uid}", json=body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... fixed={fixed}")
            time.sleep(62)

        if r2.ok:
            fixed += 1
        else:
            log.warning(f"  ✗ PUT demand/{new_uid}: {r2.status_code} {r2.text[:200]}")
            errors += 1

    log.info(f"\nИТОГ: fixed={fixed} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    main()
