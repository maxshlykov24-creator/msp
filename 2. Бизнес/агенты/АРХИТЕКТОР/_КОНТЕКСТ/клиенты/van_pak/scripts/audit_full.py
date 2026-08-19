"""
ШАГ 6: Полный аудит — сравнение сумм и количества документов old vs new для всех типов.

Типы: supply, move, invoiceout, customerorder, demand, loss, enter, paymentin, paymentout

Вывод: таблица с колонками:
  тип | old_count | new_count | old_sum | new_sum | diff_sum | diff_count | status

Запуск:
    python3 audit_full.py
"""

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

DOC_TYPES = [
    "supply", "move", "invoiceout", "customerorder",
    "demand", "loss", "enter", "paymentin", "paymentout",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "audit_full.log", encoding="utf-8"),
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
    p["limit"] = 1000
    rows, offset = [], 0
    while True:
        p["offset"] = offset
        r = api_req(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
            break
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def audit_type(doc_type, old_s, new_s, umap):
    dmap = umap.get(doc_type, {})
    filt = {"filter": f"moment>={YEAR_START}"}

    old_rows = get_all(old_s, f"entity/{doc_type}", filt)
    new_rows = get_all(new_s, f"entity/{doc_type}", filt)
    new_by_id = {uid(d["meta"]["href"]): d for d in new_rows}

    old_sum = sum((r.get("sum") or 0) for r in old_rows)
    new_sum = sum((r.get("sum") or 0) for r in new_rows)

    # Попарное сравнение по маппингу
    mismatch = []
    for old in old_rows:
        old_uid = uid(old["meta"]["href"])
        new_uid = dmap.get(old_uid)
        if not new_uid:
            continue
        n = new_by_id.get(new_uid)
        if not n:
            continue
        os = old.get("sum", 0) or 0
        ns = n.get("sum", 0) or 0
        if abs(os - ns) > 100:
            mismatch.append({
                "old_name": old.get("name"),
                "old_uid": old_uid,
                "new_uid": new_uid,
                "old_sum": os / 100,
                "new_sum": ns / 100,
                "diff": (ns - os) / 100,
            })

    status = "OK" if not mismatch and len(old_rows) == len(new_rows) else "DIFF"
    return {
        "type": doc_type,
        "old_count": len(old_rows),
        "new_count": len(new_rows),
        "old_sum_rub": old_sum / 100,
        "new_sum_rub": new_sum / 100,
        "sum_diff_rub": (new_sum - old_sum) / 100,
        "count_diff": len(new_rows) - len(old_rows),
        "pair_mismatches": len(mismatch),
        "mismatch_list": mismatch[:20],  # не более 20 примеров в отчёт
        "status": status,
    }


def main():
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    results = []
    for doc_type in DOC_TYPES:
        log.info(f"\nАудит: {doc_type}...")
        r = audit_type(doc_type, old_s, new_s, umap)
        results.append(r)
        log.info(
            f"  {doc_type}: old={r['old_count']} new={r['new_count']} "
            f"old_sum={r['old_sum_rub']:.0f}р new_sum={r['new_sum_rub']:.0f}р "
            f"diff_sum={r['sum_diff_rub']:+.0f}р pair_mism={r['pair_mismatches']} [{r['status']}]"
        )

    out = SCRIPT_DIR / "audit_full_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"\nОтчёт: {out}")

    # Краткая таблица
    log.info("\n{:<15} {:>8} {:>8} {:>14} {:>14} {:>12} {:>8}".format(
        "ТИП", "OLD", "NEW", "OLD_SUM ₽", "NEW_SUM ₽", "DIFF_SUM ₽", "STATUS"))
    log.info("-" * 90)
    for r in results:
        log.info("{:<15} {:>8} {:>8} {:>14,.0f} {:>14,.0f} {:>+12,.0f} {:>8}".format(
            r["type"], r["old_count"], r["new_count"],
            r["old_sum_rub"], r["new_sum_rub"], r["sum_diff_rub"], r["status"]))


if __name__ == "__main__":
    main()
