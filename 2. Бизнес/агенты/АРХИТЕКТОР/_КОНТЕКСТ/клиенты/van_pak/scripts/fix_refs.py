"""
Блок 1: Перенести недостающие справочники в новый аккаунт.
- Проекты (9)
- Договоры (1)
- Отделы/group (4)
- Группа товаров "Печать на термошоппере" (1)

Обновляет uuid_map.json.
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
REQUEST_SLEEP = 0.07

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_refs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api(s, method, url, **kwargs):
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=30, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url}: {e}") from e
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2 ** attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def get_all(s, path, params=None):
    rows, offset = [], 0
    p = {"limit": 1000, **(params or {})}
    while True:
        p["offset"] = offset
        r = api(s, "GET", f"{BASE}/{path}", params=p)
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def create_or_find(s_new, entity_type: str, name: str, body: dict, umap: dict, ns: str, old_uuid: str) -> str:
    """Создать сущность в новом аккаунте или найти по имени. Вернуть new_uuid."""
    # Уже в карте?
    if old_uuid in umap.get(ns, {}):
        log.info(f"  {ns}/{name}: уже в карте → {umap[ns][old_uuid]}")
        return umap[ns][old_uuid]

    # Попробуем найти по имени
    r = api(s_new, "GET", f"{BASE}/entity/{entity_type}", params={"filter": f"name={name}", "limit": 5})
    rows = r.json().get("rows", []) if r.ok else []
    for row in rows:
        if row.get("name") == name:
            new_uuid = uid(row["meta"]["href"])
            umap.setdefault(ns, {})[old_uuid] = new_uuid
            log.info(f"  {ns}/{name}: найден в new → {new_uuid}")
            return new_uuid

    # Создать
    r = api(s_new, "POST", f"{BASE}/entity/{entity_type}", json=body)
    if r.ok:
        new_uuid = uid(r.json()["meta"]["href"])
        umap.setdefault(ns, {})[old_uuid] = new_uuid
        log.info(f"  + {ns}/{name} → {new_uuid}")
        return new_uuid
    else:
        log.warning(f"  ✗ {ns}/{name}: {r.status_code} {r.text[:200]}")
        return ""


def main():
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    # ── 1. Отделы (group) ──────────────────────────────────────────────────────
    log.info("=== ОТДЕЛЫ (group) ===")
    old_groups = get_all(old_s, "entity/group")
    for g in old_groups:
        old_uuid = g["id"]
        name = g["name"]
        body = {"name": name}
        if g.get("index") is not None:
            body["index"] = g["index"]
        create_or_find(new_s, "group", name, body, umap, "group", old_uuid)

    # ── 2. Проекты ────────────────────────────────────────────────────────────
    log.info("=== ПРОЕКТЫ ===")
    old_projects = get_all(old_s, "entity/project")
    for p in old_projects:
        old_uuid = p["id"]
        name = p["name"]
        body = {"name": name}
        if p.get("description"):
            body["description"] = p["description"]
        if p.get("code"):
            body["code"] = p["code"]
        create_or_find(new_s, "project", name, body, umap, "project", old_uuid)

    # ── 3. Группы товаров (productfolder) — только недостающие ────────────────
    log.info("=== ГРУППЫ ТОВАРОВ ===")
    old_folders = get_all(old_s, "entity/productfolder")
    new_folders = get_all(new_s, "entity/productfolder")
    new_folder_by_name = {f["name"]: uid(f["meta"]["href"]) for f in new_folders}

    # Построим карту old productfolder из uuid_map (если есть)
    # Строим старый список по имени для поиска родителя
    old_folder_by_id = {f["id"]: f for f in old_folders}

    for f in old_folders:
        old_uuid = f["id"]
        name = f["name"]

        # Уже есть в new?
        if old_uuid in umap.get("productfolder", {}):
            continue
        if name in new_folder_by_name:
            umap.setdefault("productfolder", {})[old_uuid] = new_folder_by_name[name]
            continue

        # Нужно создать — найдём родителя
        body = {"name": name}
        parent_href = f.get("productFolder", {}).get("meta", {}).get("href", "")
        if parent_href:
            parent_old_uuid = uid(parent_href)
            # Новый UUID родителя — из карты или по имени
            parent_new = umap.get("productfolder", {}).get(parent_old_uuid)
            if not parent_new:
                parent_name = old_folder_by_id.get(parent_old_uuid, {}).get("name", "")
                parent_new = new_folder_by_name.get(parent_name)
            if parent_new:
                body["productFolder"] = {"meta": {
                    "href": f"{BASE}/entity/productfolder/{parent_new}",
                    "type": "productfolder", "mediaType": "application/json"}}

        new_uuid = create_or_find(new_s, "productfolder", name, body, umap, "productfolder", old_uuid)
        if new_uuid:
            new_folder_by_name[name] = new_uuid

    # Убедимся, что все old productfolder смаплены (после создания новых)
    new_folders2 = get_all(new_s, "entity/productfolder")
    new_folder_by_name2 = {f["name"]: uid(f["meta"]["href"]) for f in new_folders2}
    for f in old_folders:
        if f["id"] not in umap.get("productfolder", {}):
            new_uuid = new_folder_by_name2.get(f["name"])
            if new_uuid:
                umap.setdefault("productfolder", {})[f["id"]] = new_uuid

    # ── 4. Договоры (contract) ────────────────────────────────────────────────
    log.info("=== ДОГОВОРЫ ===")
    old_contracts = get_all(old_s, "entity/contract")
    new_contracts = get_all(new_s, "entity/contract")
    new_contract_by_name = {c["name"]: uid(c["meta"]["href"]) for c in new_contracts}

    # Получим организации из new_s для маппинга
    new_orgs = get_all(new_s, "entity/organization")
    new_org_by_uuid = {uid(o["meta"]["href"]): uid(o["meta"]["href"]) for o in new_orgs}

    for c in old_contracts:
        old_uuid = c["id"]
        name = c["name"]
        if old_uuid in umap.get("contract", {}):
            log.info(f"  contract/{name}: уже в карте")
            continue
        if name in new_contract_by_name:
            umap.setdefault("contract", {})[old_uuid] = new_contract_by_name[name]
            log.info(f"  contract/{name}: найден в new")
            continue

        # Строим тело
        body = {
            "name": name,
            "contractType": c.get("contractType", "Sales"),
        }

        # Организация
        org_href = c.get("organization", {}).get("meta", {}).get("href", "")
        if org_href:
            org_old_uuid = uid(org_href)
            org_new_uuid = umap.get("organization", {}).get(org_old_uuid)
            if org_new_uuid:
                body["organization"] = {"meta": {
                    "href": f"{BASE}/entity/organization/{org_new_uuid}",
                    "type": "organization", "mediaType": "application/json"}}

        # Агент (контрагент)
        agent_href = c.get("agent", {}).get("meta", {}).get("href", "")
        if agent_href:
            agent_type = agent_href.split("/entity/")[-1].split("/")[0]
            agent_old_uuid = uid(agent_href)
            agent_new_uuid = umap.get("counterparty", {}).get(agent_old_uuid)
            if not agent_new_uuid:
                agent_new_uuid = umap.get("organization", {}).get(agent_old_uuid)
            if agent_new_uuid:
                body["agent"] = {"meta": {
                    "href": f"{BASE}/entity/{agent_type}/{agent_new_uuid}",
                    "type": agent_type, "mediaType": "application/json"}}
            else:
                log.warning(f"  contract/{name}: агент не смаплен ({agent_old_uuid}), пропуск")
                continue

        if "organization" not in body:
            # Возьмём первую организацию из new
            if new_orgs:
                first_org = uid(new_orgs[0]["meta"]["href"])
                body["organization"] = {"meta": {
                    "href": f"{BASE}/entity/organization/{first_org}",
                    "type": "organization", "mediaType": "application/json"}}

        r = api(new_s, "POST", f"{BASE}/entity/contract", json=body)
        if r.ok:
            new_uuid = uid(r.json()["meta"]["href"])
            umap.setdefault("contract", {})[old_uuid] = new_uuid
            log.info(f"  + contract/{name} → {new_uuid}")
        else:
            log.warning(f"  ✗ contract/{name}: {r.status_code} {r.text[:200]}")

    # Сохраняем карту
    MAP_FILE.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("\n=== uuid_map сохранён ===")
    log.info(f"  project: {len(umap.get('project',{}))}")
    log.info(f"  contract: {len(umap.get('contract',{}))}")
    log.info(f"  group: {len(umap.get('group',{}))}")
    log.info(f"  productfolder: {len(umap.get('productfolder',{}))}")


if __name__ == "__main__":
    main()
