"""
Синхронизация demand.customerOrder в NEW = как в OLD (включая снятие лишних связей).

Запуск:
    python3 sync_demand_links.py --dry
    python3 sync_demand_links.py --from-date "2026-05-01 00:00:00" --to-date "2026-05-31 23:59:59"
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

OLD_TOKEN = "e95af8f2c93487a215f754ea1e5469e281613d61"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
SLEEP = 0.07

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "sync_demand_links.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def sess(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def req(s, method, url, **kwargs):
    for attempt in range(8):
        try:
            r = s.request(method, url, timeout=40, **kwargs)
        except requests.exceptions.RequestException:
            if attempt < 7:
                time.sleep(min(30, 2 ** attempt))
                continue
            raise
        if r.status_code == 429:
            time.sleep(int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000 + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2 ** attempt)
            continue
        time.sleep(SLEEP)
        return r
    return r


def get_all(s, path, params=None):
    p = dict(params or {})
    limit = 100 if p.get("expand") else 1000
    p["limit"] = limit
    rows, offset = [], 0
    while True:
        p["offset"] = offset
        r = req(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
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
    return {
        "meta": {
            "href": f"{BASE}/entity/{entity_type}/{uuid}",
            "type": entity_type,
            "mediaType": "application/json",
        }
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from-date", default=YEAR_START)
    ap.add_argument("--to-date", default=None)
    args = ap.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    dem_map = umap.get("demand", {})
    co_map = umap.get("customerorder", {})
    inv_dem = {v: k for k, v in dem_map.items()}
    old_s, new_s = sess(OLD_TOKEN), sess(NEW_TOKEN)

    filt = f"moment>={args.from_date}"
    if args.to_date:
        filt += f";moment<={args.to_date}"

    new_d = get_all(new_s, "entity/demand", {"filter": filt, "expand": "customerOrder"})
    log.info(f"NEW demand: {len(new_d)} filter={filt}")

    fixed = already = cleared = no_map = skipped = errors = 0
    put = 0

    for nd in new_d:
        new_uid = uid(nd["meta"]["href"])
        old_uid = inv_dem.get(new_uid)
        if not old_uid:
            no_map += 1
            continue

        ro = req(old_s, "GET", f"{BASE}/entity/demand/{old_uid.split('?')[0]}",
                 params={"expand": "customerOrder"})
        if not ro.ok:
            errors += 1
            continue

        old_co = (ro.json().get("customerOrder") or {}).get("meta", {}).get("href")
        cur_co = (nd.get("customerOrder") or {}).get("meta", {}).get("href")

        want = None
        if old_co:
            want = co_map.get(uid(old_co))
            if not want:
                log.warning(f"  ! demand {nd.get('name')}: old_co {uid(old_co)} не смаппирован")
                skipped += 1
                continue

        cur_uid = uid(cur_co) if cur_co else None
        if (want and cur_uid == want.split("?")[0]) or (not want and not cur_co):
            already += 1
            continue

        body = {"customerOrder": meta_obj("customerorder", want) if want else None}
        if args.dry:
            log.info(
                f"  [DRY] demand {nd.get('name')}: "
                f"{cur_uid or '—'} -> {want.split('?')[0] if want else '—'}"
            )
            fixed += 1
            if not want:
                cleared += 1
            continue

        r2 = req(new_s, "PUT", f"{BASE}/entity/demand/{new_uid}", json=body)
        put += 1
        if put % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... fixed={fixed}")
            time.sleep(62)

        if r2.ok:
            fixed += 1
            if not want:
                cleared += 1
        else:
            log.warning(f"  PUT demand/{new_uid}: {r2.status_code} {r2.text[:150]}")
            errors += 1

    report = {
        "dry": args.dry,
        "filter": filt,
        "fixed": fixed,
        "cleared": cleared,
        "already": already,
        "no_map": no_map,
        "skipped": skipped,
        "errors": errors,
    }
    (SCRIPT_DIR / "sync_demand_links_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"ИТОГ: {json.dumps(report, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
