"""
Патч externalCode + сохранение старого UUID в доп. поле.

Для каждого товара/услуги/варианта в новом аккаунте:
  1. Создаёт доп. поле «Старый UUID» (если нет).
  2. Проставляет externalCode = оригинальный externalCode из старого аккаунта.
  3. Записывает old_uuid в доп. поле «Старый UUID».
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
ATTR_NAME = "Старый UUID"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "patch_external_codes.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def get_all(session, entity_type):
    items = []
    offset = 0
    while True:
        r = session.get(f"{BASE}/entity/{entity_type}", params={"limit": 1000, "offset": offset})
        r.raise_for_status()
        rows = r.json().get("rows", [])
        items.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
        time.sleep(0.06)
    return items


def ensure_attr(session, entity_type):
    """Создать доп. поле «Старый UUID» для entity_type, если не существует. Вернуть его meta href."""
    # product и service используют один endpoint metadata
    meta_entity = "product"  # variant не имеет собственных атрибутов
    r = session.get(f"{BASE}/entity/{meta_entity}/metadata/attributes")
    r.raise_for_status()
    for attr in r.json().get("rows", []):
        if attr.get("name") == ATTR_NAME:
            log.info(f"  Доп. поле «{ATTR_NAME}» уже существует: {attr['meta']['href']}")
            return attr["meta"]["href"]
    # Создаём
    if DRY_RUN:
        log.info(f"  [DRY] Создать доп. поле «{ATTR_NAME}» для {meta_entity}")
        return None
    body = {"name": ATTR_NAME, "type": "string", "required": False}
    r2 = session.post(f"{BASE}/entity/{meta_entity}/metadata/attributes", json=body)
    r2.raise_for_status()
    href = r2.json()["meta"]["href"]
    log.info(f"  ✅ Создано доп. поле «{ATTR_NAME}»: {href}")
    return href


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip",
                           "Content-Type": "application/json"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    log.info(f"DRY_RUN: {DRY_RUN}")

    # Доп. поле создаём один раз — для product (охватывает и service)
    attr_href = ensure_attr(new_s, "product")

    for entity_type in ("product", "service"):
        old_map = umap.get(entity_type, {})
        if not old_map:
            log.info(f"{entity_type}: нет в uuid_map, пропуск")
            continue

        log.info(f"\n── {entity_type}: загружаю из старого аккаунта ──")
        old_items = get_all(old_s, entity_type)
        old_index = {
            item["meta"]["href"].split("/")[-1]: {
                "externalCode": item.get("externalCode", ""),
                "name": item.get("name", ""),
            }
            for item in old_items
        }
        log.info(f"  Загружено из src: {len(old_items)}")

        updated = skipped = errors = 0
        for old_uuid, new_uuid in old_map.items():
            info = old_index.get(old_uuid)
            if not info:
                skipped += 1
                continue

            orig_ext = info["externalCode"]
            name = info["name"]

            patch_body: dict = {}

            # 1. externalCode
            if orig_ext:
                patch_body["externalCode"] = orig_ext

            # 2. Доп. поле «Старый UUID»
            if attr_href and old_uuid:
                patch_body["attributes"] = [
                    {"meta": {"href": attr_href, "type": "attributemetadata",
                              "mediaType": "application/json"},
                     "value": old_uuid}
                ]

            if not patch_body:
                skipped += 1
                continue

            if DRY_RUN:
                log.info(f"  [DRY] PUT {entity_type}/{new_uuid} "
                         f"externalCode={orig_ext!r} oldUUID={old_uuid} ({name[:50]})")
                updated += 1
                continue

            # Пропускаем если externalCode уже верный
            try:
                cur = new_s.get(f"{BASE}/entity/{entity_type}/{new_uuid}", timeout=30)
                time.sleep(0.04)
                if cur.status_code == 200 and cur.json().get("externalCode") == orig_ext:
                    skipped += 1
                    continue
            except Exception:
                pass  # не смогли проверить — патчим

            ok = False
            for attempt in range(3):
                try:
                    r = new_s.put(f"{BASE}/entity/{entity_type}/{new_uuid}",
                                  json=patch_body, timeout=60)
                    time.sleep(0.06)
                    if r.status_code == 200:
                        updated += 1
                        ok = True
                    else:
                        log.warning(f"  ✗ PUT {entity_type}/{new_uuid} ({name[:50]}): "
                                    f"{r.status_code} {r.text[:200]}")
                        errors += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        log.warning(f"  retry {attempt+1} {entity_type}/{new_uuid}: {str(e)[:80]}")
                        time.sleep(2 ** attempt)
                    else:
                        log.warning(f"  ✗ PUT {entity_type}/{new_uuid}: {str(e)[:150]}")
                        errors += 1

            if (updated + errors) % 100 == 0 and (updated + errors) > 0:
                log.info(f"  Прогресс {entity_type}: обновлено={updated}, "
                         f"пропущено={skipped}, ошибок={errors}")

        log.info(f"  {entity_type}: обновлено={updated}, пропущено={skipped}, ошибок={errors}")

    log.info("\nГотово.")


if __name__ == "__main__":
    main()
