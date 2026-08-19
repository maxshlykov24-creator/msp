"""
Проставляет основное поле salesChannel (Канал продаж) в заказах покупателей
по значению доп. поля «Канал продаж» (customentity).
"""

import logging
import sys
import time
from pathlib import Path

import requests

NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
DRY_RUN = "--dry-run" in sys.argv
ATTR_NAME = "Канал продаж"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "patch_sales_channel.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def patch_with_retry(session, url, body):
    for attempt in range(3):
        try:
            r = session.put(url, json=body, timeout=60)
            time.sleep(0.07)
            return r
        except Exception as e:
            if attempt < 2:
                log.warning(f"  retry {attempt + 1}: {str(e)[:80]}")
                time.sleep(2 ** attempt)
            else:
                raise


def get_attr_channel_name(order: dict):
    for attr in order.get("attributes", []):
        if attr.get("name") != ATTR_NAME:
            continue
        value = attr.get("value")
        if isinstance(value, dict):
            return value.get("name") or None
        if value:
            return str(value)
    return None


def main():
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {NEW_TOKEN}",
        "Accept-Encoding": "gzip",
    })
    log.info(f"DRY_RUN: {DRY_RUN}")

    # name -> saleschannel meta
    r = session.get(f"{BASE}/entity/saleschannel", params={"limit": 100}, timeout=30)
    r.raise_for_status()
    channels = {
        ch["name"]: ch["meta"]
        for ch in r.json().get("rows", [])
    }
    log.info(f"Каналы продаж: {list(channels.keys())}")

    updated = skipped = errors = no_attr = 0
    offset = 0
    total = None

    while True:
        params = {
            "limit": 100,
            "offset": offset,
            "expand": "salesChannel,attributes",
            "filter": "moment>2026-01-01 00:00:00",
        }
        resp = session.get(f"{BASE}/entity/customerorder", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if total is None:
            total = data.get("meta", {}).get("size", 0)
            log.info(f"Заказов 2026: {total}")

        if not rows:
            break

        for order in rows:
            name = order.get("name", "?")
            order_uuid = order["meta"]["href"].split("/")[-1].split("?")[0]
            channel_name = get_attr_channel_name(order)
            if not channel_name:
                no_attr += 1
                continue

            current = (order.get("salesChannel") or {}).get("name")
            if current == channel_name:
                skipped += 1
                continue

            channel_meta = channels.get(channel_name)
            if not channel_meta:
                log.warning(f"  ✗ {name}: нет saleschannel с именем {channel_name!r}")
                errors += 1
                continue

            body = {"salesChannel": {"meta": channel_meta}}
            if DRY_RUN:
                log.info(f"  [DRY] {name}: salesChannel → {channel_name!r} (было {current!r})")
                updated += 1
                continue

            r_put = patch_with_retry(
                session,
                f"{BASE}/entity/customerorder/{order_uuid}",
                body,
            )
            if r_put.status_code == 200:
                updated += 1
            else:
                log.warning(f"  ✗ {name}: {r_put.status_code} {r_put.text[:200]}")
                errors += 1

        offset += len(rows)
        if (updated + errors) and (updated + errors) % 100 == 0:
            log.info(f"  Прогресс: обновлено={updated}, пропущено={skipped}, без атрибута={no_attr}, ошибок={errors}")
        if len(rows) < 100:
            break
        time.sleep(0.05)

    log.info(
        f"\nГотово: обновлено={updated}, уже совпадало={skipped}, "
        f"без атрибута={no_attr}, ошибок={errors}"
    )


if __name__ == "__main__":
    main()
