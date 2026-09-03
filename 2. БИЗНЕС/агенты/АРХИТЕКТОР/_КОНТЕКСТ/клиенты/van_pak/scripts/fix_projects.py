"""
Блок 1а: Проставить project и contract во все документы 2026,
где в старом аккаунте они были заполнены, а в новом — нет.

Типы: customerorder, demand, invoiceout, supply, purchaseorder,
      purchasereturn, paymentin, paymentout, cashin, cashout,
      move, loss, enter, factureout, processingorder
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

DOC_TYPES = [
    "customerorder", "demand", "invoiceout", "supply",
    "purchaseorder", "purchasereturn", "paymentin", "paymentout",
    "cashin", "cashout", "move", "loss", "enter",
    "factureout", "processingorder",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_projects.log", encoding="utf-8"),
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
            r = s.request(method, url, timeout=40, **kwargs)
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


def get_all(s, path, params=None):
    rows, offset = [], 0
    p = {"limit": 1000, **(params or {})}
    while True:
        p["offset"] = offset
        r = api(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
            log.warning(f"GET {path}: {r.status_code} {r.text[:150]}")
            break
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def process_doc_type(doc_type: str, old_s, new_s, umap: dict) -> dict:
    log.info(f"\n--- {doc_type} ---")
    stat = {"updated": 0, "already_ok": 0, "no_project": 0, "no_map": 0, "errors": 0}

    # Загрузить old с expand project и contract
    params = {"filter": f"moment>={YEAR_START}", "expand": "project,contract"}
    try:
        old_docs = get_all(old_s, f"entity/{doc_type}", params)
    except Exception as e:
        log.warning(f"  Не удалось загрузить old {doc_type}: {e}")
        return stat

    if not old_docs:
        log.info(f"  {doc_type}: нет документов в old за 2026")
        return stat

    # Загрузить new (нужны текущие project/contract)
    try:
        new_docs = get_all(new_s, f"entity/{doc_type}",
                           {"filter": f"moment>={YEAR_START}", "expand": "project,contract"})
    except Exception as e:
        log.warning(f"  Не удалось загрузить new {doc_type}: {e}")
        return stat

    new_by_uuid = {uid(d["meta"]["href"]): d for d in new_docs}
    ns = doc_type  # пространство в uuid_map

    put_count = 0
    for old in old_docs:
        old_uuid = uid(old["meta"]["href"])

        # Найти new uuid
        new_uuid = umap.get(ns, {}).get(old_uuid)
        if not new_uuid:
            # Fallback: externalCode = old_uuid
            stat["no_map"] += 1
            continue

        new_doc = new_by_uuid.get(new_uuid)

        # Что проставлять?
        patch = {}

        # project
        old_proj_href = old.get("project", {}).get("meta", {}).get("href", "") if old.get("project") else ""
        if old_proj_href:
            old_proj_uuid = uid(old_proj_href)
            new_proj_uuid = umap.get("project", {}).get(old_proj_uuid)
            if new_proj_uuid:
                # Проверим, уже проставлен ли в new
                cur_proj = (new_doc or {}).get("project", {})
                cur_proj_href = cur_proj.get("meta", {}).get("href", "") if cur_proj else ""
                if uid(cur_proj_href) != new_proj_uuid:
                    patch["project"] = {"meta": {
                        "href": f"{BASE}/entity/project/{new_proj_uuid}",
                        "type": "project", "mediaType": "application/json"}}
            else:
                log.warning(f"  project не в карте: {old_proj_uuid}")

        # contract
        old_con_href = old.get("contract", {}).get("meta", {}).get("href", "") if old.get("contract") else ""
        if old_con_href:
            old_con_uuid = uid(old_con_href)
            new_con_uuid = umap.get("contract", {}).get(old_con_uuid)
            if new_con_uuid:
                cur_con = (new_doc or {}).get("contract", {})
                cur_con_href = cur_con.get("meta", {}).get("href", "") if cur_con else ""
                if uid(cur_con_href) != new_con_uuid:
                    patch["contract"] = {"meta": {
                        "href": f"{BASE}/entity/contract/{new_con_uuid}",
                        "type": "contract", "mediaType": "application/json"}}

        if not old_proj_href and not old_con_href:
            stat["no_project"] += 1
            continue

        if not patch:
            stat["already_ok"] += 1
            continue

        # PUT
        r = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_uuid}", json=patch)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... обновлено {stat['updated']}")
            time.sleep(62)

        if r.ok:
            stat["updated"] += 1
        else:
            log.warning(f"  ✗ {doc_type}/{new_uuid}: {r.text[:150]}")
            stat["errors"] += 1

    log.info(f"  {doc_type}: обновлено={stat['updated']} уже_ок={stat['already_ok']} "
             f"нет_проекта={stat['no_project']} нет_в_карте={stat['no_map']} ошибок={stat['errors']}")
    return stat


def main():
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    total = {"updated": 0, "already_ok": 0, "no_project": 0, "no_map": 0, "errors": 0}
    report = {}

    for doc_type in DOC_TYPES:
        stat = process_doc_type(doc_type, old_s, new_s, umap)
        report[doc_type] = stat
        for k in total:
            total[k] += stat[k]

    log.info(f"\n{'='*60}")
    log.info(f"ИТОГО: обновлено={total['updated']} уже_ок={total['already_ok']} "
             f"нет_проекта={total['no_project']} нет_в_карте={total['no_map']} ошибок={total['errors']}")
    log.info(f"{'='*60}")

    out = SCRIPT_DIR / "fix_projects_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Отчёт: {out}")


if __name__ == "__main__":
    main()
