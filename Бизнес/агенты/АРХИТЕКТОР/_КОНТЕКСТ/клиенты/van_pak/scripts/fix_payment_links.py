"""
ШАГ 3: Восстановить все связи платежей (operations) в new аккаунте точно как в old.

Логика:
  1. Загрузить все paymentin + paymentout 2026 из old.
  2. Для каждого old платежа взять old.operations -> список операций (demand, supply и т.д.)
  3. Смапить: old_op_uuid -> new_op_uuid через uuid_map[entity_type].
  4. Смапить: old_payment_uuid -> new_payment_uuid через uuid_map[entity_type].
  5. PUT new_payment с {"operations": [...смапленные...]} если отличается от текущего.

Запуск:
    python3 fix_payment_links.py --dry
    python3 fix_payment_links.py
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
REQUEST_SLEEP = 0.07

PAY_TYPES = ["paymentin", "paymentout"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_payment_links.log", encoding="utf-8"),
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


def operations_key(ops):
    """Уникальный ключ для набора operations (для сравнения)."""
    return frozenset(uid((op.get("meta") or {}).get("href", "")) for op in ops if op)


def process_pay_type(pay_type, old_s, new_s, umap, dry):
    log.info(f"\n{'='*55}\n{pay_type}\n{'='*55}")
    pay_map = umap.get(pay_type, {})  # old -> new

    old_pays = get_all(old_s, f"entity/{pay_type}",
                       {"filter": f"moment>={YEAR_START}", "expand": "operations"})
    new_pays = get_all(new_s, f"entity/{pay_type}",
                       {"filter": f"moment>={YEAR_START}", "expand": "operations"})
    new_by_id = {uid(d["meta"]["href"]): d for d in new_pays}

    log.info(f"  old={len(old_pays)} new={len(new_pays)}")

    fixed = already_ok = no_map = skipped = errors = 0
    put_count = 0

    for old in old_pays:
        old_uid = uid(old["meta"]["href"])
        new_uid = pay_map.get(old_uid)
        if not new_uid:
            no_map += 1
            continue

        n = new_by_id.get(new_uid)
        if not n:
            no_map += 1
            continue

        old_ops = old.get("operations") or []
        new_ops = n.get("operations") or []

        if operations_key(old_ops) == operations_key(new_ops):
            already_ok += 1
            continue

        if not old_ops:
            already_ok += 1
            continue

        # Смапить каждую операцию
        mapped_ops = []
        skip_this = False
        for op in old_ops:
            op_meta = (op.get("meta") or {})
            op_type = op_meta.get("type", "")
            old_op_uid = uid(op_meta.get("href", ""))
            new_op_uid = umap.get(op_type, {}).get(old_op_uid)
            if not new_op_uid:
                log.warning(f"  ! {pay_type}/{old.get('name','?')}: операция {op_type}/{old_op_uid} не смаппирована")
                skip_this = True
                break
            entry = {"meta": {
                "href": f"{BASE}/entity/{op_type}/{new_op_uid}",
                "type": op_type,
                "mediaType": "application/json",
            }}
            if "linkedSum" in op:
                entry["linkedSum"] = op["linkedSum"]
            mapped_ops.append(entry)

        if skip_this:
            skipped += 1
            continue

        if dry:
            log.info(f"  [DRY] {pay_type}/{old.get('name','?')}: обновить operations ({len(mapped_ops)} шт.)")
            fixed += 1
            continue

        body = {"operations": mapped_ops}
        r = api_req(new_s, "PUT", f"{BASE}/entity/{pay_type}/{new_uid}", json=body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... fixed={fixed}")
            time.sleep(62)

        if r.ok:
            fixed += 1
        else:
            log.warning(f"  ✗ PUT {pay_type}/{new_uid}: {r.status_code} {r.text[:200]}")
            errors += 1

    log.info(f"  ИТОГ {pay_type}: fixed={fixed} already_ok={already_ok} no_map={no_map} skipped={skipped} errors={errors}")
    return {"fixed": fixed, "already_ok": already_ok, "no_map": no_map, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--only", default=None, help="paymentin или paymentout")
    args = parser.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    types = [args.only] if args.only else PAY_TYPES
    report = {}
    for pt in types:
        report[pt] = process_pay_type(pt, old_s, new_s, umap, args.dry)

    out = SCRIPT_DIR / "fix_payment_links_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()
