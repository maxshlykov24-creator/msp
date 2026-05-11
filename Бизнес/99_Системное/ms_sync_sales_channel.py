#!/usr/bin/env python3
"""
Синхронизация доп. атрибута «Канал продаж» (customentity) с базовым salesChannel
для заказов покупателя за календарный 2026 год.

Правила:
- salesChannel «Действующий клиент» -> доп. поле элемент «Действующий клиент»
- salesChannel «WANG PACK SHOP» -> доп. поле элемент «WANG PACK SHOP»
- Остальные каналы и заказы без salesChannel не меняем.

Важно: в API expand=salesChannel при limit>100 часто не отдаёт id — пагинация limit=100.

Metadata (снято с API целевого аккаунта, discovery 2026-04):
- Базовый канал: стандартное поле документа salesChannel (entity saleschannel).
- Доп. поле: атрибут customerorder name «Канал продаж», type customentity, ATTR_ID ниже.
- Элементы справочника customentity CE_ID совпадают по названию с каналами saleschannel.

Вебхуки UPDATE не отключаем: префиксный webhook менял name, на атрибуты не влиял.
"""

import argparse
import os
import sys
import time
from datetime import datetime

import requests
from requests.exceptions import ReadTimeout, ConnectTimeout

TOKEN = os.environ.get("MS_TOKEN", "")
API_BASE = "https://api.moysklad.ru/api/remap/1.2"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Content-Type": "application/json",
}
RATE_LIMIT_MS = 150

# Базовые каналы продаж (entity saleschannel)
SALES_DC = "36ab2b1f-e2c3-11ef-0a80-06b800055057"  # Действующий клиент
SALES_WP = "c9915c15-381e-11ee-0a80-032000082f4d"  # WANG PACK SHOP

# Доп. поле «Канал продаж» (customerorder attribute)
ATTR_ID = "64b7b3fc-e2c6-11ef-0a80-06b80005ec32"
ATTR_META_HREF = f"{API_BASE}/entity/customerorder/metadata/attributes/{ATTR_ID}"

# Справочник элементов того же названия (customentity)
CE_ID = "5acea32d-e2c6-11ef-0a80-00ad00053197"
ELEM_DC = "82ddba6c-e2c6-11ef-0a80-14e000050251"
ELEM_WP = "79d135d3-e2c6-11ef-0a80-08b900055a27"

# Целевой value для атрибута (как в ответах API)
ATTR_VALUE = {
    SALES_DC: {
        "meta": {
            "href": f"{API_BASE}/entity/customentity/{CE_ID}/{ELEM_DC}",
            "metadataHref": f"{API_BASE}/context/companysettings/metadata/customEntities/{CE_ID}",
            "type": "customentity",
            "mediaType": "application/json",
        },
        "name": "Действующий клиент",
    },
    SALES_WP: {
        "meta": {
            "href": f"{API_BASE}/entity/customentity/{CE_ID}/{ELEM_WP}",
            "metadataHref": f"{API_BASE}/context/companysettings/metadata/customEntities/{CE_ID}",
            "type": "customentity",
            "mediaType": "application/json",
        },
        "name": "WANG PACK SHOP",
    },
}

FILTER_2026 = "moment>=2026-01-01 00:00:00;moment<=2026-12-31 23:59:59"


def api_get(url_or_path, params=None):
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(6):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=120)
        except (ReadTimeout, ConnectTimeout) as e:
            time.sleep(min(2 ** attempt, 30))
            if attempt == 5:
                raise RuntimeError(f"GET timeout: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("GET failed")


def api_put(url_or_path, body):
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = requests.put(url, headers=HEADERS, json=body, timeout=120)
        except (ReadTimeout, ConnectTimeout) as e:
            time.sleep(min(2 ** attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"PUT timeout: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        if r.status_code == 412:
            err = r.json().get("errors", [{}])[0].get("error", "")
            raise RuntimeError(f"412: {err}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("PUT failed")


def fetch_orders_2026():
    """Список заказов с expand=salesChannel (limit<=100)."""
    rows = []
    offset = 0
    while True:
        d = api_get(
            "/entity/customerorder",
            {
                "filter": FILTER_2026,
                "limit": 100,
                "offset": offset,
                "expand": "salesChannel",
            },
        )
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
        time.sleep(0.25)
    return rows


def current_element_id(order):
    for a in order.get("attributes", []):
        if a.get("id") == ATTR_ID:
            v = a.get("value")
            if isinstance(v, dict):
                return v.get("meta", {}).get("href", "").rstrip("/").split("/")[-1]
            return None
    return None


def want_element_id(sales_id):
    if sales_id == SALES_DC:
        return ELEM_DC
    if sales_id == SALES_WP:
        return ELEM_WP
    return None


def build_updated_attributes(full_order, new_value_dict):
    attrs = [dict(a) for a in full_order.get("attributes", [])]
    found = False
    for i, a in enumerate(attrs):
        if a.get("id") == ATTR_ID:
            attrs[i] = {
                "meta": {
                    "href": ATTR_META_HREF,
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "id": ATTR_ID,
                "name": "Канал продаж",
                "value": new_value_dict,
            }
            found = True
            break
    if not found:
        attrs.append(
            {
                "meta": {
                    "href": ATTR_META_HREF,
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "id": ATTR_ID,
                "name": "Канал продаж",
                "value": new_value_dict,
            }
        )
    return attrs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["report", "fix"], default="report")
    args = parser.parse_args()
    if not TOKEN:
        print("Задайте MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None
    print("Загрузка заказов 2026 (moment)...", flush=True)
    orders = fetch_orders_2026()
    print(f"  Всего заказов: {len(orders)}", flush=True)

    to_fix = []
    skipped_no_sc = 0
    skipped_other_sc = 0
    already_ok = 0

    for o in orders:
        sc = o.get("salesChannel")
        if not sc or not sc.get("id"):
            skipped_no_sc += 1
            continue
        sid = sc["id"]
        want_el = want_element_id(sid)
        if not want_el:
            skipped_other_sc += 1
            continue
        cur_el = current_element_id(o)
        if cur_el == want_el:
            already_ok += 1
            continue
        to_fix.append(
            {
                "id": o["id"],
                "name": o.get("name", "?"),
                "sales_name": sc.get("name"),
                "sales_id": sid,
                "cur_el": cur_el,
                "want_el": want_el,
            }
        )

    print(f"  Уже совпадают: {already_ok}", flush=True)
    print(f"  Без salesChannel: {skipped_no_sc}", flush=True)
    print(f"  Другой канал (не DC/WP): {skipped_other_sc}", flush=True)
    print(f"  К обновлению: {len(to_fix)}", flush=True)

    if to_fix:
        print("\nПримеры (до 20):", flush=True)
        for r in to_fix[:20]:
            print(
                f"  {r['name']} | sc={r['sales_name']} | cur={r['cur_el']} -> {r['want_el']}",
                flush=True,
            )

    out_dir = os.environ.get("MS_OUTPUT_DIR", "/tmp")
    report_path = os.path.join(out_dir, f"ms_sales_channel_sync_{ts}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Режим: {args.mode}\n")
        f.write(f"Всего заказов 2026: {len(orders)}\n")
        f.write(f"К обновлению: {len(to_fix)}\n\n")
        for r in to_fix:
            f.write(f"{r['name']} id={r['id']} {r['cur_el']} -> {r['want_el']}\n")
    print(f"\nОтчёт: {report_path}", flush=True)

    if args.mode == "report":
        print("Для применения: --mode fix", flush=True)
        return

    if not to_fix:
        print("Нечего обновлять.", flush=True)
        return

    log_path = os.path.join(os.environ.get("MS_OUTPUT_DIR", "/tmp"), f"ms_sales_channel_fix_{ts}.log")
    ok = err = 0
    with open(log_path, "w", encoding="utf-8") as log:
        for i, rec in enumerate(to_fix):
            oid = rec["id"]
            sales_id = rec["sales_id"]
            val = ATTR_VALUE[sales_id]
            try:
                full = api_get(
                    f"/entity/customerorder/{oid}",
                    {"expand": "salesChannel"},
                )
                time.sleep(RATE_LIMIT_MS / 1000)
                body = {
                    "meta": full["meta"],
                    "attributes": build_updated_attributes(full, val),
                }
                api_put(f"/entity/customerorder/{oid}", body)
                log.write(f"OK {rec['name']} {oid}\n")
                log.flush()
                ok += 1
            except Exception as e:
                log.write(f"FAIL {rec['name']} {oid}: {e}\n")
                log.flush()
                err += 1
            time.sleep(RATE_LIMIT_MS / 1000)
            if (i + 1) % 50 == 0:
                print(f"  ... {i+1}/{len(to_fix)}", flush=True)

    print(f"\nГотово: OK={ok} ERR={err}", flush=True)
    print(f"Лог: {log_path}", flush=True)


if __name__ == "__main__":
    main()
