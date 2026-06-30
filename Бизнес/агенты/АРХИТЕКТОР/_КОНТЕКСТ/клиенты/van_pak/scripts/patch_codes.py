"""
Синхронизирует поле code (артикул/код) товаров, услуг и вариантов из старого аккаунта в новый.
Использует externalCode (= оригинальный externalCode из старого аккаунта, проставленный ранее)
как ключ сопоставления.
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
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
DRY_RUN = "--dry-run" in sys.argv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "patch_codes.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def get_all(session, entity_type):
    items, offset = [], 0
    while True:
        r = session.get(f"{BASE}/entity/{entity_type}",
                        params={"limit": 1000, "offset": offset}, timeout=60)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        items.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
        time.sleep(0.06)
    return items


def patch_with_retry(session, url, body):
    for attempt in range(3):
        try:
            r = session.put(url, json=body, timeout=60)
            time.sleep(0.06)
            return r
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    log.info(f"DRY_RUN: {DRY_RUN}")

    for entity_type in ("product", "service", "variant"):
        entity_map = umap.get(entity_type, {})
        if not entity_map:
            log.info(f"{entity_type}: нет в uuid_map, пропуск")
            continue

        log.info(f"\n── {entity_type} ──")
        old_items = get_all(old_s, entity_type)
        # Индекс: old_uuid → code
        old_index = {
            item["meta"]["href"].split("/")[-1]: item.get("code", "")
            for item in old_items
        }
        log.info(f"  Загружено из src: {len(old_items)}, с кодом: "
                 f"{sum(1 for c in old_index.values() if c)}")

        updated = skipped = errors = 0
        for old_uuid, new_uuid in entity_map.items():
            old_code = old_index.get(old_uuid, "")
            if not old_code:
                skipped += 1
                continue

            if DRY_RUN:
                log.info(f"  [DRY] {entity_type}/{new_uuid} code={old_code!r}")
                updated += 1
                continue

            r = patch_with_retry(new_s, f"{BASE}/entity/{entity_type}/{new_uuid}",
                                  {"code": old_code})
            if r.status_code == 200:
                updated += 1
            elif r.status_code == 412 and "уникальности" in r.text:
                # Код уже занят другим товаром в новом аккаунте
                log.warning(f"  ⚠ конфликт code={old_code!r} для {entity_type}/{new_uuid}: "
                             f"уже используется другим объектом")
                errors += 1
            else:
                log.warning(f"  ✗ {entity_type}/{new_uuid}: {r.status_code} {r.text[:150]}")
                errors += 1

            if (updated + errors) % 200 == 0 and (updated + errors) > 0:
                log.info(f"  Прогресс: обновлено={updated}, пропущено={skipped}, ошибок={errors}")

        log.info(f"  {entity_type}: обновлено={updated}, пропущено={skipped}, ошибок={errors}")

    log.info("\nГотово.")


if __name__ == "__main__":
    main()
