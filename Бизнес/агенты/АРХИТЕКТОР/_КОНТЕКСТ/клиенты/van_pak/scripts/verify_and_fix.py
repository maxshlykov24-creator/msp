"""
Верификация полноты и позиций для customerorder и demand.
При расхождениях — добавляет недостающие позиции через POST /positions.

Использование:
    python3 verify_and_fix.py            # только отчёт
    python3 verify_and_fix.py --fix      # фиксить позиции
    python3 verify_and_fix.py --type demand   # только demand
    python3 verify_and_fix.py --type customerorder  # только заказы
"""
import argparse
import json
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"
UMAP_PATH = Path(__file__).parent / "uuid_map.json"


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def req_get(s: requests.Session, url: str, params: dict = None, retries: int = 5) -> dict:
    for attempt in range(retries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return {}


def req_post(s: requests.Session, url: str, body, retries: int = 5):
    for attempt in range(retries):
        try:
            r = s.post(url, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if not r.ok:
                print(f"    POST {url} → {r.status_code}: {r.text[:200]}")
                return None
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    POST failed: {e}")
                return None
    return None


def fetch_all(s: requests.Session, entity: str, extra_params: dict = None) -> list:
    params = {"limit": 1000, "offset": 0, **(extra_params or {})}
    result = []
    while True:
        data = req_get(s, f"{BASE}/entity/{entity}", params)
        rows = data.get("rows", [])
        result.extend(rows)
        if len(rows) < 1000:
            break
        params = {**params, "offset": params["offset"] + 1000}
        time.sleep(0.15)
    return result


def uid_of(pos: dict) -> str:
    href = pos.get("assortment", {}).get("meta", {}).get("href", "")
    return href.split("/")[-1].split("?")[0]


def verify_entity(
    old_s, new_s, entity_type: str, umap: dict, all_assort_map: dict,
    fix: bool = False
) -> dict:
    print(f"\n=== {entity_type} ===")
    entity_map = umap.get(entity_type, {})

    print(f"Загружаю {entity_type} 2026 из СТАРОГО...", flush=True)
    old_docs = fetch_all(old_s, entity_type, {"filter": f"moment>={YEAR_START}"})
    old_by_uuid = {d["meta"]["href"].split("/")[-1]: d for d in old_docs}
    print(f"  Старый: {len(old_docs)}")

    print(f"Загружаю {entity_type} 2026 из НОВОГО...", flush=True)
    new_docs = fetch_all(new_s, entity_type, {"filter": f"moment>={YEAR_START}"})
    new_by_uuid = {d["meta"]["href"].split("/")[-1]: d for d in new_docs}
    print(f"  Новый: {len(new_docs)}")

    # Отсутствующие в новом
    missing_docs = []
    for old_uuid, doc in old_by_uuid.items():
        new_uuid = entity_map.get(old_uuid)
        if not new_uuid or new_uuid not in new_by_uuid:
            missing_docs.append({"name": doc.get("name"), "moment": doc.get("moment"),
                                  "old_uuid": old_uuid, "new_uuid": new_uuid})

    print(f"  Отсутствует в новом: {len(missing_docs)}")
    for m in missing_docs[:10]:
        print(f"    {m['name']}  moment={m['moment']}")

    # Проверка позиций
    paired = [
        (old_uuid, entity_map[old_uuid])
        for old_uuid in old_by_uuid
        if old_uuid in entity_map and entity_map[old_uuid] in new_by_uuid
    ]
    print(f"\nПроверяю позиции в {len(paired)} парных {entity_type}...")

    ok = 0
    mismatch_list = []
    fixed_count = 0

    for i, (old_uuid, new_uuid) in enumerate(paired):
        if i % 100 == 0 and i > 0:
            print(f"  {i}/{len(paired)}...", flush=True)

        old_pos = req_get(
            old_s, f"{BASE}/entity/{entity_type}/{old_uuid}/positions",
            params={"limit": 1000}
        ).get("rows", [])
        time.sleep(0.1)
        new_pos = req_get(
            new_s, f"{BASE}/entity/{entity_type}/{new_uuid}/positions",
            params={"limit": 1000}
        ).get("rows", [])
        time.sleep(0.1)

        old_set = set()
        for pos in old_pos:
            old_a = uid_of(pos)
            new_a = all_assort_map.get(old_a, old_a)
            old_set.add((new_a, round(pos.get("quantity", 0))))

        new_set = {(uid_of(pos), round(pos.get("quantity", 0))) for pos in new_pos}

        if old_set == new_set:
            ok += 1
        else:
            missing_pos = old_set - new_set
            extra_pos = new_set - old_set
            mismatch_list.append({
                "name": old_by_uuid[old_uuid].get("name"),
                "old_uuid": old_uuid,
                "new_uuid": new_uuid,
                "old_count": len(old_pos),
                "new_count": len(new_pos),
                "missing_count": len(missing_pos),
                "extra_count": len(extra_pos),
            })

            # Фикс: добавляем недостающие позиции
            if fix and missing_pos:
                new_uid_set = {uid_of(p) for p in new_pos}
                to_add = []
                for pos in old_pos:
                    old_a = uid_of(pos)
                    new_a = all_assort_map.get(old_a)
                    if not new_a or new_a in new_uid_set:
                        continue
                    # Определяем тип ассортимента
                    a_href = pos.get("assortment", {}).get("meta", {}).get("href", "")
                    a_type = a_href.split("/entity/")[-1].split("/")[0] if "/entity/" in a_href else "product"
                    # Маппим UOM если есть
                    uom_href = (pos.get("uom") or {}).get("meta", {}).get("href", "")
                    new_uom = umap.get("uom", {}).get(uom_href.split("/")[-1])
                    pos_body = {
                        "assortment": {
                            "meta": {
                                "href": f"{BASE}/entity/{a_type}/{new_a}",
                                "type": a_type,
                                "mediaType": "application/json",
                            }
                        },
                        "quantity": pos.get("quantity", 1),
                        "price": pos.get("price", 0),
                        "discount": pos.get("discount", 0),
                        "vat": pos.get("vat", 0),
                    }
                    if new_uom:
                        pos_body["uom"] = {
                            "meta": {
                                "href": f"{BASE}/entity/uom/{new_uom}",
                                "type": "uom",
                                "mediaType": "application/json",
                            }
                        }
                    to_add.append(pos_body)
                if to_add:
                    r = req_post(
                        new_s,
                        f"{BASE}/entity/{entity_type}/{new_uuid}/positions",
                        to_add,
                    )
                    if r:
                        fixed_count += 1

    print(f"\n=== Итог {entity_type} ===")
    print(f"  Совпадают:    {ok}/{len(paired)}")
    print(f"  Расхождения:  {len(mismatch_list)}")
    if fix:
        print(f"  Пофикшено:    {fixed_count}")
    if mismatch_list:
        print("\n  Топ расхождений:")
        for m in mismatch_list[:15]:
            print(f"    {m['name']}: old={m['old_count']} new={m['new_count']} "
                  f"missing={m['missing_count']} extra={m['extra_count']}")

    return {
        "entity_type": entity_type,
        "old_total": len(old_docs),
        "new_total": len(new_docs),
        "missing_docs": missing_docs,
        "paired": len(paired),
        "ok": ok,
        "mismatches": mismatch_list,
        "fixed": fixed_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Добавить недостающие позиции")
    parser.add_argument("--type", choices=["customerorder", "demand", "both"],
                        default="both")
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    umap = json.loads(UMAP_PATH.read_text(encoding="utf-8"))
    all_assort_map = {
        **umap.get("product", {}),
        **umap.get("service", {}),
        **umap.get("variant", {}),
    }

    results = []
    types_to_check = (
        ["customerorder"] if args.type == "customerorder"
        else ["demand"] if args.type == "demand"
        else ["customerorder", "demand"]
    )

    for entity_type in types_to_check:
        r = verify_entity(old_s, new_s, entity_type, umap, all_assort_map, fix=args.fix)
        results.append(r)

    # Итоговая таблица
    print("\n" + "=" * 60)
    print("СВОДНАЯ ТАБЛИЦА (src vs dst 2026)")
    print("=" * 60)
    for r in results:
        et = r["entity_type"]
        missing_docs_n = len(r["missing_docs"])
        print(f"  {et:20} src={r['old_total']:5} dst={r['new_total']:5} "
              f"missing_docs={missing_docs_n:4} "
              f"pos_ok={r['ok']:5}/{r['paired']:5} "
              f"pos_mismatch={len(r['mismatches']):4}")

    # Сохранить отчёт
    out = Path(__file__).parent / "verify_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()
