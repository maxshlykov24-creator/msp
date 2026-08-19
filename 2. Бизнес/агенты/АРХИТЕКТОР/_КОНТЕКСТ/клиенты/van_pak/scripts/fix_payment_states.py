"""
Проставка статусов paymentin в новом аккаунте по образцу старого.
Работает по uuid_map["paymentin"] — для каждого old-платежа со статусом,
если в новом статус отличается/отсутствует — делает PUT.
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
YEAR_START = "2026-01-01 00:00:00"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REQUEST_SLEEP = 0.07

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_payment_states.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api_req(s, method, url, **kwargs):
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=40, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url}: {e}") from e
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            log.warning(f"429 rate-limit, жду {wait:.1f}с")
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"{method} {url} — провал после 6 попыток")


def get_all(s, path, extra_params=None):
    params = {"limit": 1000, "offset": 0, **(extra_params or {})}
    rows = []
    while True:
        r = api_req(s, "GET", f"{BASE}/{path}", params=params)
        if not r.ok:
            raise RuntimeError(f"GET {path} → {r.status_code}: {r.text[:200]}")
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        log.info(f"  {path}: {len(rows)}/{data.get('meta',{}).get('size',0)}")
        if len(chunk) < 1000:
            break
        params["offset"] += 1000
        time.sleep(0.1)
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def main():
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    pay_map = umap.get("paymentin", {})

    # Статусы
    log.info("Загружаю метаданные статусов...")
    old_meta = api_req(old_s, "GET", f"{BASE}/entity/paymentin/metadata").json()
    new_meta = api_req(new_s, "GET", f"{BASE}/entity/paymentin/metadata").json()
    old_states = {uid(s["meta"]["href"]): s["name"] for s in old_meta.get("states", [])}
    new_states = {s["name"]: uid(s["meta"]["href"]) for s in new_meta.get("states", [])}
    log.info(f"  OLD states: {old_states}")
    log.info(f"  NEW states: {new_states}")

    # Загрузка old и new платежей
    log.info("Загружаю OLD paymentin 2026...")
    old_all = get_all(old_s, "entity/paymentin", {"filter": f"moment>={YEAR_START}"})
    log.info("Загружаю NEW paymentin 2026...")
    new_all = get_all(new_s, "entity/paymentin", {"filter": f"moment>={YEAR_START}"})
    new_state_by_uuid = {uid(p["meta"]["href"]): uid((p.get("state") or {}).get("meta", {}).get("href", "")) for p in new_all}

    updated = already_ok = no_map = no_state_in_old = skipped = 0
    put_count = 0

    for old in old_all:
        old_uuid = uid(old["meta"]["href"])
        state_href = (old.get("state") or {}).get("meta", {}).get("href", "")
        if not state_href:
            no_state_in_old += 1
            continue

        old_state_name = old_states.get(uid(state_href))
        if not old_state_name:
            no_state_in_old += 1
            continue

        new_uuid = pay_map.get(old_uuid)
        if not new_uuid:
            no_map += 1
            continue

        new_state_uuid = new_states.get(old_state_name)
        if not new_state_uuid:
            log.warning(f"  Статус '{old_state_name}' не найден в new")
            skipped += 1
            continue

        current_new_state = new_state_by_uuid.get(new_uuid, "")
        if current_new_state == new_state_uuid:
            already_ok += 1
            continue

        # PUT
        state_body = {"state": {"meta": {
            "href": f"{BASE}/entity/paymentin/metadata/states/{new_state_uuid}",
            "type": "state", "mediaType": "application/json"}}}
        r = api_req(new_s, "PUT", f"{BASE}/entity/paymentin/{new_uuid}", json=state_body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... обновлено {updated}")
            time.sleep(62)

        if r.ok:
            updated += 1
            if updated % 100 == 0:
                log.info(f"  Обновлено статусов: {updated}")
        else:
            log.warning(f"  ✗ PUT paymentin/{new_uuid}: {r.text[:150]}")
            skipped += 1

    log.info(f"\n{'='*50}")
    log.info(f"Обновлено: {updated} | Уже_ок: {already_ok} | Нет_статуса_old: {no_state_in_old} | "
             f"Нет_в_карте: {no_map} | Пропущено: {skipped}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()
