"""
Блок 3: Создать 11 сотрудников в новом аккаунте и проставить
доп. поле «Ответственный» во всех типах документов 2026.

Сотрудники создаются без доступа (без логина) — не занимают платные места.
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
    "processingorder",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_employees.log", encoding="utf-8"),
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
    p = dict(params or {})
    # С expand limit=100 (ограничение API МойСклад)
    limit = 100 if p.get("expand") else 1000
    p["limit"] = limit
    rows, offset = [], 0
    while True:
        p["offset"] = offset
        r = api(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
            log.warning(f"GET {path}: {r.status_code} {r.text[:150]}")
            break
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        total = data.get("meta", {}).get("size", 0)
        if offset == 0 and total > 0:
            log.info(f"  {path}: 0/{total}")
        if len(chunk) < limit:
            break
        offset += limit
        if offset % 500 == 0:
            log.info(f"  {path}: {offset}/{total}")
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


# ── 1. Создать сотрудников ─────────────────────────────────────────────────

def migrate_employees(old_s, new_s, umap: dict):
    log.info("=== СОЗДАНИЕ СОТРУДНИКОВ ===")

    old_employees = get_all(old_s, "entity/employee")
    new_employees = get_all(new_s, "entity/employee")

    # Индекс new по email и по имени
    new_by_email = {}
    new_by_name = {}
    for e in new_employees:
        em = (e.get("email") or "").lower().strip()
        nm = e.get("fullName") or e.get("name") or ""
        new_uuid = uid(e["meta"]["href"])
        if em:
            new_by_email[em] = new_uuid
        if nm:
            new_by_name[nm] = new_uuid

    # Карта old groups -> new groups для отдела
    group_map = umap.get("group", {})

    for emp in old_employees:
        old_uuid = emp["id"]
        name = emp.get("name") or ""
        full_name = emp.get("fullName") or name
        email = (emp.get("email") or "").lower().strip()

        if old_uuid in umap.get("employee", {}):
            log.info(f"  employee/{name}: уже в карте")
            continue

        # Найти по email или имени
        new_uuid = None
        if email and email in new_by_email:
            new_uuid = new_by_email[email]
        elif full_name in new_by_name:
            new_uuid = new_by_name[full_name]
        elif name in new_by_name:
            new_uuid = new_by_name[name]

        if new_uuid:
            umap.setdefault("employee", {})[old_uuid] = new_uuid
            log.info(f"  employee/{name}: найден в new → {new_uuid}")
            continue

        # Создать — lastName обязателен
        last_name = emp.get("lastName") or name.split()[0]
        body = {
            "lastName": last_name,
            "firstName": emp.get("firstName") or "",
        }
        if emp.get("middleName"):
            body["middleName"] = emp["middleName"]
        if email:
            body["email"] = email
        if emp.get("position"):
            body["position"] = emp["position"]
        if emp.get("phone"):
            body["phone"] = emp["phone"]

        # Отдел
        group_href = emp.get("group", {}).get("meta", {}).get("href", "")
        if group_href:
            g_old_uuid = uid(group_href)
            g_new_uuid = group_map.get(g_old_uuid)
            if g_new_uuid:
                body["group"] = {"meta": {
                    "href": f"{BASE}/entity/group/{g_new_uuid}",
                    "type": "group", "mediaType": "application/json"}}

        r = api(new_s, "POST", f"{BASE}/entity/employee", json=body)
        if r.ok:
            new_uuid = uid(r.json()["meta"]["href"])
            umap.setdefault("employee", {})[old_uuid] = new_uuid
            new_by_email[email] = new_uuid
            new_by_name[full_name] = new_uuid
            log.info(f"  + employee/{name} → {new_uuid}")
        else:
            log.warning(f"  ✗ employee/{name}: {r.status_code} {r.text[:200]}")

    log.info(f"  Итого в карте: {len(umap.get('employee', {}))}")


# ── 2. Найти атрибут «Ответственный» ──────────────────────────────────────

def find_responsible_attr(s_new, doc_type: str) -> str:
    """Вернуть UUID атрибута 'Ответственный' для типа документа."""
    r = api(s_new, "GET", f"{BASE}/entity/{doc_type}/metadata/attributes",
            params={"limit": 100})
    if not r.ok:
        return ""
    for a in r.json().get("rows", []):
        if not isinstance(a, dict):
            continue
        if a.get("name") in ("Ответственный", "Ответственный менеджер"):
            return uid(a["meta"]["href"])
    return ""


# ── 3. Проставить Ответственного в документах ────────────────────────────

def get_old_responsible_attr_uuid(s_old, doc_type: str) -> str:
    """UUID атрибута «Ответственный» в OLD аккаунте."""
    r = api(s_old, "GET", f"{BASE}/entity/{doc_type}/metadata/attributes",
            params={"limit": 100})
    if not r.ok:
        return ""
    for a in r.json().get("rows", []):
        if not isinstance(a, dict):
            continue
        if a.get("name") in ("Ответственный", "Ответственный менеджер"):
            return uid(a["meta"]["href"])
    return ""


def fill_responsible(doc_type: str, old_s, new_s, umap: dict, attr_uuid: str, old_attr_uuid: str = ""):
    log.info(f"\n--- {doc_type} (new_attr={attr_uuid} old_attr={old_attr_uuid}) ---")
    stat = {"updated": 0, "already_ok": 0, "no_attr": 0, "no_map": 0, "errors": 0}

    if not attr_uuid:
        log.info(f"  Атрибут «Ответственный» не найден для {doc_type}, пропуск")
        stat["no_attr"] = 1
        return stat

    try:
        old_docs = get_all(old_s, f"entity/{doc_type}",
                           {"filter": f"moment>={YEAR_START}", "expand": "attributes"})
    except Exception as e:
        log.warning(f"  Не удалось загрузить old {doc_type}: {e}")
        return stat

    # Получим new с их атрибутами для сравнения
    try:
        new_docs = get_all(new_s, f"entity/{doc_type}",
                           {"filter": f"moment>={YEAR_START}", "expand": "attributes"})
    except Exception as e:
        log.warning(f"  Не удалось загрузить new {doc_type}: {e}")
        return stat

    # Индекс текущего Ответственного в new
    new_responsible = {}
    for d in new_docs:
        d_uuid = uid(d["meta"]["href"])
        for a in d.get("attributes", []):
            if uid(a.get("meta", {}).get("href", "")) == attr_uuid:
                val_href = (a.get("value") or {}).get("meta", {}).get("href", "")
                new_responsible[d_uuid] = uid(val_href)

    put_count = 0
    for old in old_docs:
        old_uuid = uid(old["meta"]["href"])
        new_uuid = umap.get(doc_type, {}).get(old_uuid)
        if not new_uuid:
            stat["no_map"] += 1
            continue

        # Ищем Ответственного в old атрибутах (по имени ИЛИ по href атрибута)
        old_emp_uuid = None
        for a in old.get("attributes", []):
            if not isinstance(a, dict):
                continue
            attr_href = a.get("meta", {}).get("href", "")
            a_name = a.get("name", "")
            if uid(attr_href) == old_attr_uuid or a_name in ("Ответственный", "Ответственный менеджер"):
                val = a.get("value")
                if val and isinstance(val, dict):
                    val_href = val.get("meta", {}).get("href", "")
                    old_emp_uuid = uid(val_href)
                break

        if not old_emp_uuid:
            stat["no_attr"] += 1
            continue

        # Смапить на new
        new_emp_uuid = umap.get("employee", {}).get(old_emp_uuid)
        if not new_emp_uuid:
            stat["no_map"] += 1
            continue

        # Сравнить с текущим
        if new_responsible.get(new_uuid) == new_emp_uuid:
            stat["already_ok"] += 1
            continue

        # PUT
        patch = {"attributes": [{"meta": {
            "href": f"{BASE}/entity/{doc_type}/metadata/attributes/{attr_uuid}",
            "type": "attributemetadata", "mediaType": "application/json"},
            "value": {"meta": {
                "href": f"{BASE}/entity/employee/{new_emp_uuid}",
                "type": "employee", "mediaType": "application/json"}}}]}

        r = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_uuid}", json=patch)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... обновлено {stat['updated']}")
            time.sleep(62)

        if r.ok:
            stat["updated"] += 1
            if stat["updated"] % 200 == 0:
                log.info(f"  Обновлено: {stat['updated']}")
        else:
            log.warning(f"  ✗ {doc_type}/{new_uuid}: {r.text[:150]}")
            stat["errors"] += 1

    log.info(f"  {doc_type}: обновлено={stat['updated']} уже_ок={stat['already_ok']} "
             f"нет_атр={stat['no_attr']} нет_в_карте={stat['no_map']} ошибок={stat['errors']}")
    return stat


def main():
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    # 1. Создать / смапить сотрудников
    migrate_employees(old_s, new_s, umap)
    MAP_FILE.write_text(json.dumps(umap, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("uuid_map сохранён (после сотрудников)")

    # 2. Проставить «Ответственного» во все типы
    total = {"updated": 0, "already_ok": 0, "no_attr": 0, "no_map": 0, "errors": 0}
    report = {}

    for doc_type in DOC_TYPES:
        attr_uuid = find_responsible_attr(new_s, doc_type)
        old_attr_uuid = get_old_responsible_attr_uuid(old_s, doc_type)
        stat = fill_responsible(doc_type, old_s, new_s, umap, attr_uuid, old_attr_uuid)
        report[doc_type] = stat
        for k in total:
            total[k] += stat[k]

    log.info(f"\n{'='*60}")
    log.info(f"ИТОГО: обновлено={total['updated']} уже_ок={total['already_ok']} "
             f"нет_атр={total['no_attr']} нет_в_карте={total['no_map']} ошибок={total['errors']}")
    log.info(f"{'='*60}")

    out = SCRIPT_DIR / "fix_employees_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Отчёт: {out}")


if __name__ == "__main__":
    main()
