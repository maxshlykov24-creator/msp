"""
Записывает старый UUID в поле «Описание» каждой услуги в новом аккаунте.
Формат: если описание было пустым — просто UUID.
Если было заполнено — добавляет в конец: \n[Старый UUID: ...]
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
        logging.FileHandler(SCRIPT_DIR / "patch_service_descriptions.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    svc_map = umap.get("service", {})  # old_uuid -> new_uuid
    log.info(f"DRY_RUN: {DRY_RUN}")
    log.info(f"Услуг в uuid_map: {len(svc_map)}")

    # Загружаем услуги из старого аккаунта
    r = old_s.get(f"{BASE}/entity/service", params={"limit": 1000})
    r.raise_for_status()
    old_services = {
        item["meta"]["href"].split("/")[-1]: item
        for item in r.json().get("rows", [])
    }
    log.info(f"Загружено из src: {len(old_services)}")

    updated = skipped = errors = 0
    for old_uuid, new_uuid in svc_map.items():
        old_svc = old_services.get(old_uuid)
        if not old_svc:
            skipped += 1
            continue

        name = old_svc.get("name", "")
        old_desc = old_svc.get("description", "") or ""

        # Формируем новое описание
        tag = f"[Старый UUID: {old_uuid}]"
        if old_desc:
            new_desc = f"{old_desc}\n{tag}"
        else:
            new_desc = tag

        if DRY_RUN:
            log.info(f"  [DRY] PUT service/{new_uuid} description={new_desc!r} ({name[:50]})")
            updated += 1
            continue

        r2 = new_s.put(
            f"{BASE}/entity/service/{new_uuid}",
            json={"description": new_desc},
            timeout=30,
        )
        time.sleep(0.06)
        if r2.status_code == 200:
            updated += 1
        else:
            log.warning(f"  ✗ PUT service/{new_uuid} ({name[:50]}): "
                        f"{r2.status_code} {r2.text[:200]}")
            errors += 1

    log.info(f"\nГотово: обновлено={updated}, пропущено={skipped}, ошибок={errors}")


if __name__ == "__main__":
    main()
