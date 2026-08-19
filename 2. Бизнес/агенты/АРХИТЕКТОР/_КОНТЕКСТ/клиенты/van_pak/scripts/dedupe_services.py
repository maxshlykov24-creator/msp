"""
Удаление дублей услуг в новом аккаунте МойСклад.

Проблема: pilot + full прогоны создали по 2 копии каждой услуги.
Все 48 UUID canonical в uuid_map, поэтому cleanup_duplicates.py не помогает.

Стратегия:
  1. Найти группы услуг с одинаковым именем в dst.
  2. Для каждой группы: оставить NEW UUID, на который указывает больше всего
     старых UUID в uuid_map (= "основной"). Второй — удалить.
  3. Перезаписать uuid_map: все old UUID из группы → один выбранный new UUID.
  4. DELETE дубля в dst.
"""

import json
import logging
import sys
import time
from collections import defaultdict
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
        logging.FileHandler(SCRIPT_DIR / "dedupe_services.log", encoding="utf-8"),
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
    svc_map = umap.get("service", {})

    # Инвертируем: new_uuid -> список old_uuid
    new_to_olds = defaultdict(list)
    for old, new in svc_map.items():
        new_to_olds[new].append(old)

    log.info(f"DRY_RUN: {DRY_RUN}")

    # Все услуги в dst
    all_services = []
    offset = 0
    while True:
        r = session.get(f"{BASE}/entity/service", params={"limit": 1000, "offset": offset})
        r.raise_for_status()
        rows = r.json().get("rows", [])
        all_services.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
        time.sleep(0.06)
    log.info(f"Услуг в dst: {len(all_services)}")

    # Группируем по имени
    by_name = defaultdict(list)
    for svc in all_services:
        by_name[svc["name"]].append(svc)

    dupes = {name: svcs for name, svcs in by_name.items() if len(svcs) > 1}
    log.info(f"Групп с дублями: {len(dupes)}")

    deleted = 0
    remapped = 0

    for name, svcs in dupes.items():
        # Выбираем «основной» — тот new_uuid, на который указывает больше old UUIDs
        best = max(svcs, key=lambda s: len(new_to_olds.get(s["meta"]["href"].split("/")[-1], [])))
        best_uuid = best["meta"]["href"].split("/")[-1]
        others = [s for s in svcs if s["meta"]["href"].split("/")[-1] != best_uuid]

        log.info(f'  "{name}": оставляем {best_uuid}, удаляем {len(others)} дублей')

        # Все old_uuid этой группы -> best_uuid
        all_olds_in_group = []
        for s in svcs:
            uid = s["meta"]["href"].split("/")[-1]
            all_olds_in_group.extend(new_to_olds.get(uid, []))

        if not DRY_RUN:
            for old_uid in all_olds_in_group:
                umap["service"][old_uid] = best_uuid
            remapped += len(all_olds_in_group)

        # Удаляем дубли
        for dup in others:
            dup_uuid = dup["meta"]["href"].split("/")[-1]
            if DRY_RUN:
                log.info(f"    [DRY] DELETE service/{dup_uuid}")
                deleted += 1
                continue
            try:
                r = session.delete(f"{BASE}/entity/service/{dup_uuid}", timeout=30)
                time.sleep(0.06)
                if r.status_code == 200:
                    deleted += 1
                    # Убираем из uuid_map значения, которые теперь перенаправлены
                else:
                    log.warning(f"    ✗ DELETE service/{dup_uuid}: {r.status_code} {r.text[:200]}")
            except Exception as e:
                log.warning(f"    ✗ DELETE service/{dup_uuid}: {str(e)[:150]}")

    if not DRY_RUN:
        tmp = MAP_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(MAP_FILE)
        log.info(f"uuid_map сохранён. Remapped old UUIDs: {remapped}")

    log.info(f"Дублей удалено: {deleted}")


if __name__ == "__main__":
    main()
