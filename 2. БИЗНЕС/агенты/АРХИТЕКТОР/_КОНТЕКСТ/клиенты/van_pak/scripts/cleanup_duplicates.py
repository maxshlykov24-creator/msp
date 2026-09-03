"""
Удаление orphan-товаров в новом аккаунте МойСклад.
Orphan = продукт чей UUID не является canonical value в uuid_map.
Эти товары — дубли от первого прогона (created with code=...).
"""

import json
import time
import logging
import sys
from pathlib import Path

import requests

NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
DRY_RUN = "--dry-run" in sys.argv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "cleanup.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def main():
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {NEW_TOKEN}",
        "Accept-Encoding": "gzip",
    })

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    canonical_products = set(umap.get("product", {}).values())
    canonical_services = set(umap.get("service", {}).values())
    log.info(f"Canonical products: {len(canonical_products)}, services: {len(canonical_services)}")
    log.info(f"DRY_RUN: {DRY_RUN}")

    for entity_type, canonical in [("product", canonical_products), ("service", canonical_services)]:
        all_items = []
        offset = 0
        while True:
            r = session.get(f"{BASE}/entity/{entity_type}", params={"limit": 1000, "offset": offset})
            r.raise_for_status()
            rows = r.json().get("rows", [])
            all_items.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000
            time.sleep(0.06)

        orphans = [
            item for item in all_items
            if item["meta"]["href"].split("/")[-1] not in canonical
        ]
        log.info(f"{entity_type}: всего={len(all_items)}, canonical={len(canonical)}, orphans={len(orphans)}")

        deleted = 0
        failed = 0
        for item in orphans:
            uid = item["meta"]["href"].split("/")[-1]
            name = item.get("name", "?")
            code = item.get("code", "")
            if DRY_RUN:
                log.info(f"  [DRY] DELETE {entity_type}/{uid} name={name} code={code}")
                deleted += 1
                continue
            try:
                r = session.delete(f"{BASE}/entity/{entity_type}/{uid}", timeout=30)
                time.sleep(0.06)
                if r.status_code == 200:
                    deleted += 1
                    if deleted % 50 == 0:
                        log.info(f"  Удалено {deleted}/{len(orphans)}")
                else:
                    log.warning(f"  ✗ {entity_type}/{uid} ({name}): {r.status_code} {r.text[:200]}")
                    failed += 1
            except Exception as e:
                log.warning(f"  ✗ {entity_type}/{uid}: {str(e)[:150]}")
                failed += 1

        log.info(f"  {entity_type}: удалено={deleted}, ошибок={failed}")


if __name__ == "__main__":
    main()
