"""
clean_ms.py — Удалить все отгрузки (Demand) и товары (Product) из МойСклад.
Запускать перед чистовой загрузкой номенклатуры.

Использование:
  python3 clean_ms.py           # удалить всё
  python3 clean_ms.py --dry-run # только показать, без удаления
"""

import requests, time, argparse

TOKEN = "951f3b09451c02691686ac8077cc59d474685a04"
BASE  = "https://api.moysklad.ru/api/remap/1.2"
H     = {"Authorization": f"Bearer {TOKEN}"}

def req(method, url, retries=5, **kwargs):
    kwargs.setdefault("timeout", 40)
    for attempt in range(1, retries + 1):
        try:
            return requests.request(method, url, headers=H, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"    сеть (попытка {attempt}/{retries}): {type(e).__name__}")
            if attempt < retries:
                time.sleep(3 * attempt)
    return None

def delete_all(entity, dry_run):
    total_deleted = 0
    while True:
        r = req("GET", f"{BASE}/entity/{entity}?limit=100")
        if r is None:
            print(f"  Не удалось получить список {entity} — пропуск")
            break
        rows = r.json().get("rows", [])
        if not rows:
            break
        for row in rows:
            name = (row.get("name") or row.get("id") or "?")[:50]
            if dry_run:
                print(f"  [DRY] {entity} / {name}")
            else:
                resp = req("DELETE", f"{BASE}/entity/{entity}/{row['id']}")
                code = resp.status_code if resp is not None else "FAIL"
                print(f"  DELETE {entity} {name[:40]} → {code}")
                time.sleep(0.2)
            total_deleted += 1
    return total_deleted

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "УДАЛЕНИЕ"
    print(f"\n=== {mode} ===\n")

    n_demands = delete_all("demand", args.dry_run)
    print(f"\nОтгрузок: {n_demands}")

    n_products = delete_all("product", args.dry_run)
    print(f"Товаров:  {n_products}")

    print(f"\nГотово ({mode}).")

if __name__ == "__main__":
    main()
