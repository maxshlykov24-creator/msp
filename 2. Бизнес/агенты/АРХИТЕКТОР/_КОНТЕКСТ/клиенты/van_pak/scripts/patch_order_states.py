"""
Синхронизирует статусы и флаг «Проведено» заказов покупателей из старого аккаунта в новый.

Шаги:
  1. Получить все статусы из старого аккаунта.
  2. Создать в новом аккаунте отсутствующие статусы (сопоставление по имени).
  3. Построить маппинг old_state_uuid → new_state_uuid.
  4. Пройтись по всем заказам старого аккаунта (expand=state), найти соответствующий
     заказ в новом через uuid_map и PATCH: applicable + state.
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
        logging.FileHandler(SCRIPT_DIR / "patch_order_states.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def get_all_orders(session, params=None):
    items = []
    offset = 0
    base_params = {"limit": 100, "expand": "state", **(params or {})}
    while True:
        base_params["offset"] = offset
        r = session.get(f"{BASE}/entity/customerorder", params=base_params, timeout=60)
        r.raise_for_status()
        data = r.json()
        rows = data.get("rows", [])
        items.extend(rows)
        total = data.get("meta", {}).get("size", 0)
        if offset == 0:
            log.info(f"  Всего заказов: {total}")
        if len(rows) < 100:
            break
        offset += 100
        time.sleep(0.07)
    return items


def patch_with_retry(session, url, body):
    for attempt in range(3):
        try:
            r = session.put(url, json=body, timeout=60)
            time.sleep(0.07)
            return r
        except Exception as e:
            if attempt < 2:
                log.warning(f"  retry {attempt+1}: {str(e)[:80]}")
                time.sleep(2 ** attempt)
            else:
                raise


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    order_map = umap.get("customerorder", {})  # old_uuid → new_uuid
    log.info(f"DRY_RUN: {DRY_RUN}")
    log.info(f"Заказов в uuid_map: {len(order_map)}")

    # ── Шаг 1: статусы старого аккаунта ──────────────────────────────────────
    log.info("\n── Шаг 1: статусы старого аккаунта ──")
    r = old_s.get(f"{BASE}/entity/customerorder/metadata", timeout=30)
    r.raise_for_status()
    old_states = r.json().get("states", [])
    log.info(f"Статусов в старом: {len(old_states)}")

    # ── Шаг 2: статусы нового аккаунта, создаём недостающие ─────────────────
    log.info("\n── Шаг 2: статусы нового аккаунта ──")
    r2 = new_s.get(f"{BASE}/entity/customerorder/metadata", timeout=30)
    r2.raise_for_status()
    new_states_raw = r2.json().get("states", [])
    new_states_by_name = {s["name"]: s for s in new_states_raw}
    log.info(f"Статусов в новом: {len(new_states_raw)}")

    # Маппинг old_state_uuid → new_state_uuid
    state_map: dict[str, str] = {}

    for st in old_states:
        old_st_uuid = st["meta"]["href"].split("/")[-1]
        name = st["name"]
        st_type = st["stateType"]  # Regular / Successful / Unsuccessful

        if name in new_states_by_name:
            new_st_uuid = new_states_by_name[name]["meta"]["href"].split("/")[-1]
            state_map[old_st_uuid] = new_st_uuid
            log.info(f"  ✓ существует: [{st_type}] {name}")
        else:
            if DRY_RUN:
                log.info(f"  [DRY] создать: [{st_type}] {name}")
                state_map[old_st_uuid] = f"DRY_{old_st_uuid}"
                continue
            body = {"name": name, "stateType": st_type}
            if st.get("color"):
                body["color"] = st["color"]
            r3 = new_s.post(
                f"{BASE}/entity/customerorder/metadata/states",
                json=body, timeout=30,
            )
            time.sleep(0.12)
            if r3.status_code in (200, 201):
                new_st_uuid = r3.json()["meta"]["href"].split("/")[-1]
                state_map[old_st_uuid] = new_st_uuid
                new_states_by_name[name] = r3.json()
                log.info(f"  + создан: [{st_type}] {name}")
            else:
                log.warning(f"  ✗ создание [{st_type}] {name}: {r3.status_code} {r3.text[:150]}")

    log.info(f"\nМаппинг статусов: {len(state_map)} из {len(old_states)}")

    # ── Шаг 3: патч заказов ──────────────────────────────────────────────────
    log.info("\n── Шаг 3: патч заказов (applicable + state) ──")
    old_orders = get_all_orders(old_s, {"filter": "moment>2026-01-01 00:00:00"})

    updated = skipped = errors = 0
    for order in old_orders:
        old_uuid = order["meta"]["href"].split("/")[-1].split("?")[0]
        new_uuid = order_map.get(old_uuid)
        if not new_uuid:
            skipped += 1
            continue

        applicable = order.get("applicable", False)
        state_href = (order.get("state") or {}).get("meta", {}).get("href", "")
        old_st_uuid = state_href.split("/")[-1].split("?")[0] if state_href else None

        patch_body: dict = {"applicable": applicable}

        if old_st_uuid and old_st_uuid in state_map:
            new_st_uuid = state_map[old_st_uuid]
            if not new_st_uuid.startswith("DRY_"):
                patch_body["state"] = {
                    "meta": {
                        "href": f"{BASE}/entity/customerorder/metadata/states/{new_st_uuid}",
                        "type": "state",
                        "mediaType": "application/json",
                    }
                }

        name = order.get("name", "")
        if DRY_RUN:
            st_name = (order.get("state") or {}).get("name", "—")
            log.info(f"  [DRY] {name}: applicable={applicable}, state={st_name!r}")
            updated += 1
            continue

        try:
            r = patch_with_retry(new_s, f"{BASE}/entity/customerorder/{new_uuid}", patch_body)
            if r.status_code == 200:
                updated += 1
            else:
                log.warning(f"  ✗ {name}: {r.status_code} {r.text[:200]}")
                errors += 1
        except Exception as e:
            log.warning(f"  ✗ {name}: {str(e)[:150]}")
            errors += 1

        if (updated + errors) % 100 == 0 and (updated + errors) > 0:
            log.info(f"  Прогресс: обновлено={updated}, пропущено={skipped}, ошибок={errors}")

    log.info(f"\nГотово: обновлено={updated}, пропущено={skipped}, ошибок={errors}")


if __name__ == "__main__":
    main()
