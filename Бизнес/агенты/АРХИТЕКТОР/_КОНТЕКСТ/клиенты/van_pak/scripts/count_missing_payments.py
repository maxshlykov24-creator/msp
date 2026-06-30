"""
Точный подсчёт недостающих платежей paymentin за 2026 (без создания).
Сверяет old (через uuid_map) с реально существующими в новом аккаунте.
"""
import json
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"


def sess(tok):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip"})
    return s


def get(s, url, params=None, retries=6):
    for a in range(retries):
        try:
            r = s.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(2 ** a + 1)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if a < retries - 1:
                time.sleep(2 ** a)
            else:
                raise
    return None


def uid(h):
    return h.split("/")[-1].split("?")[0] if h else ""


def fetch_all(s, entity, params_extra=None):
    params = {"limit": 1000, "offset": 0, **(params_extra or {})}
    rows = []
    while True:
        d = get(s, f"{BASE}/entity/{entity}", params)
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        params = {**params, "offset": params["offset"] + 1000}
        time.sleep(0.1)
    return rows


def main():
    old_s, new_s = sess(OLD_TOKEN), sess(NEW_TOKEN)
    umap = json.loads((Path(__file__).parent / "uuid_map.json").read_text(encoding="utf-8"))
    pmap = umap.get("paymentin", {})

    print("Загружаю paymentin 2026 OLD...", flush=True)
    old = fetch_all(old_s, "paymentin", {"filter": f"moment>={YEAR_START}"})
    print(f"  OLD: {len(old)}")

    print("Загружаю paymentin 2026 NEW...", flush=True)
    new = fetch_all(new_s, "paymentin", {"filter": f"moment>={YEAR_START}"})
    new_uuids = {uid(d["meta"]["href"]) for d in new}
    print(f"  NEW: {len(new)}")

    missing = []          # нет new_uuid в карте вообще
    stale_map = []        # есть в карте, но new_uuid не существует в аккаунте
    present = 0
    for d in old:
        old_uuid = uid(d["meta"]["href"])
        new_uuid = pmap.get(old_uuid)
        if not new_uuid:
            missing.append(d)
        elif new_uuid not in new_uuids:
            stale_map.append(d)
        else:
            present += 1

    print(f"\nИтог по paymentin 2026:")
    print(f"  реально существуют в новом:        {present}")
    print(f"  нет в карте (никогда не создавались): {len(missing)}")
    print(f"  карта ссылается на несуществующий:    {len(stale_map)}")
    print(f"  ВСЕГО отсутствует:                    {len(missing) + len(stale_map)}")

    sample = (missing + stale_map)[:25]
    print(f"\nПримеры отсутствующих ({len(sample)} из {len(missing)+len(stale_map)}):")
    total_sum = 0
    for d in (missing + stale_map):
        total_sum += d.get("sum", 0) or 0
    for d in sample:
        print(f"  {d.get('name'):14} moment={d.get('moment')} sum={d.get('sum',0)/100:.2f}")
    print(f"\nСуммарно по отсутствующим платежам: {total_sum/100:,.2f} ₽")

    out = Path(__file__).parent / "missing_payments.json"
    out.write_text(json.dumps({
        "missing": [{"name": d.get("name"), "uuid": uid(d["meta"]["href"]),
                     "moment": d.get("moment"), "sum": d.get("sum")} for d in missing],
        "stale_map": [{"name": d.get("name"), "uuid": uid(d["meta"]["href"]),
                       "moment": d.get("moment"), "sum": d.get("sum")} for d in stale_map],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСписок сохранён: {out}")


if __name__ == "__main__":
    main()
