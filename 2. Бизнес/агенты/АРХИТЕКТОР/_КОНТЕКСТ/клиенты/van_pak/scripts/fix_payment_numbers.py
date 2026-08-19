"""
ШАГ 4: Восстановить оригинальные номера (name + incomingNumber/outgoingNumber) платежей.

Проблема: у части paymentin name автоматически присвоен новый (например "00800"),
а в old был другой (например "БС-12345").
incomingNumber — банковский номер — тоже может отличаться.

Логика:
  1. Загрузить paymentin + paymentout 2026 из old и new.
  2. Для каждого old платежа найти new через uuid_map.
  3. Если old.name != new.name или incomingNumber/outgoingNumber отличается — PUT.

Запуск:
    python3 fix_payment_numbers.py --dry
    python3 fix_payment_numbers.py
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

PAY_TYPES = ["paymentin", "paymentout"]
# Поля номера для каждого типа
NUM_FIELD = {"paymentin": "incomingNumber", "paymentout": "outgoingNumber"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_payment_numbers.log", encoding="utf-8"),
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


def process_pay_type(pay_type, old_s, new_s, umap, dry):
    log.info(f"\n{'='*55}\n{pay_type}\n{'='*55}")
    pay_map = umap.get(pay_type, {})
    num_field = NUM_FIELD[pay_type]

    old_pays = get_all(old_s, f"entity/{pay_type}", {"filter": f"moment>={YEAR_START}"})
    new_pays = get_all(new_s, f"entity/{pay_type}", {"filter": f"moment>={YEAR_START}"})
    new_by_id = {uid(d["meta"]["href"]): d for d in new_pays}

    log.info(f"  old={len(old_pays)} new={len(new_pays)}")

    fixed = already_ok = no_map = errors = 0
    put_count = 0

    for old in old_pays:
        old_uid_v = uid(old["meta"]["href"])
        new_uid_v = pay_map.get(old_uid_v)
        if not new_uid_v:
            no_map += 1
            continue
        n = new_by_id.get(new_uid_v)
        if not n:
            no_map += 1
            continue

        old_num = old.get(num_field, "") or ""
        new_num = n.get(num_field, "") or ""

        if old_num == new_num:
            already_ok += 1
            continue

        body = {}
        if old_num != new_num:
            body[num_field] = old_num

        if dry:
            log.info(f"  [DRY] {pay_type}/{new_uid_v}: {num_field} '{new_num}'->'{old_num}'")
            fixed += 1
            continue

        r = api_req(new_s, "PUT", f"{BASE}/entity/{pay_type}/{new_uid_v}", json=body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... fixed={fixed}")
            time.sleep(62)

        if r.ok:
            fixed += 1
        else:
            log.warning(f"  ✗ PUT {pay_type}/{new_uid_v}: {r.status_code} {r.text[:200]}")
            errors += 1

    log.info(f"  ИТОГ {pay_type}: fixed={fixed} already_ok={already_ok} no_map={no_map} errors={errors}")
    return {"fixed": fixed, "already_ok": already_ok, "no_map": no_map, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    types = [args.only] if args.only else PAY_TYPES
    report = {}
    for pt in types:
        report[pt] = process_pay_type(pt, old_s, new_s, umap, args.dry)

    out = SCRIPT_DIR / "fix_payment_numbers_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()
