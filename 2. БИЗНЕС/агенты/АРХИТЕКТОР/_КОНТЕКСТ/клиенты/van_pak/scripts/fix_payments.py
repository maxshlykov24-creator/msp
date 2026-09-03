"""
Починка paymentin ВАН ПАК.
Фаза 1: bulk-загрузка old/new + карта статусов.
Фаза 2: чистка коллизий в uuid_map["paymentin"].
Фаза 3: создать ~728 пропавших платежей.
Фаза 4: перепроверить и восстановить связи ВСЕХ платежей.
Фаза 5: финальный отчёт.

Запуск:
    python3 fix_payments.py              # полный прогон
    python3 fix_payments.py --smoke 3   # пробный прогон на N платежах
"""

import argparse
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
        logging.FileHandler(SCRIPT_DIR / "fix_payments.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api_req(s: requests.Session, method: str, url: str, **kwargs):
    """Устойчивый запрос с ретраями. Возвращает Response или бросает RuntimeError."""
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=40, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} — сетевой сбой: {e}") from e
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


def get_all(s: requests.Session, path: str, extra_params: dict = None) -> list:
    """Пагинированная выгрузка, limit=1000."""
    params = {"limit": 1000, "offset": 0, **(extra_params or {})}
    rows = []
    while True:
        r = api_req(s, "GET", f"{BASE}/{path}", params=params)
        if not r.ok:
            raise RuntimeError(f"GET {path} → {r.status_code}: {r.text[:300]}")
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        total = data.get("meta", {}).get("size", 0)
        log.info(f"  {path}: загружено {len(rows)}/{total}")
        if len(chunk) < 1000:
            break
        params["offset"] += 1000
        time.sleep(0.1)
    return rows


def uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


def meta_obj(entity_type: str, uuid: str) -> dict:
    return {
        "meta": {
            "href": f"{BASE}/entity/{entity_type}/{uuid}",
            "type": entity_type,
            "mediaType": "application/json",
        }
    }


# ── uuid_map ──────────────────────────────────────────────────────────────────

class UMap:
    def __init__(self):
        self.data = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    def get(self, ns: str, key: str):
        return self.data.get(ns, {}).get(key)

    def set(self, ns: str, key: str, val: str):
        self.data.setdefault(ns, {})[key] = val

    def save(self):
        MAP_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("  uuid_map сохранён")


# ── Фаза 1: загрузка данных ───────────────────────────────────────────────────

def load_data(old_s, new_s):
    log.info("=== ФАЗА 1: ЗАГРУЗКА ДАННЫХ ===")

    # Статусы paymentin
    log.info("Загружаю метаданные статусов...")
    old_meta = api_req(old_s, "GET", f"{BASE}/entity/paymentin/metadata").json()
    new_meta = api_req(new_s, "GET", f"{BASE}/entity/paymentin/metadata").json()

    old_states_by_uuid = {uid(s["meta"]["href"]): s for s in old_meta.get("states", [])}
    new_states_by_name = {s["name"]: uid(s["meta"]["href"]) for s in new_meta.get("states", [])}

    # Создать недостающие статусы в новом
    for st in old_meta.get("states", []):
        name = st.get("name")
        if name and name not in new_states_by_name:
            body = {
                "name": name,
                "stateType": st.get("stateType", "Regular"),
                "color": st.get("color", 10066329),
            }
            r = api_req(new_s, "POST", f"{BASE}/entity/paymentin/metadata/states", json=body)
            if r.ok:
                new_uuid = uid(r.json()["meta"]["href"])
                new_states_by_name[name] = new_uuid
                log.info(f"  + статус '{name}'")
            else:
                log.warning(f"  ✗ статус '{name}': {r.text[:150]}")

    log.info(f"Статусы OLD: {list(old_states_by_uuid.values())[:5]}")
    log.info(f"Статусы NEW: {new_states_by_name}")

    # Старые платежи 2026
    log.info("Загружаю paymentin OLD 2026...")
    old_payments = get_all(old_s, "entity/paymentin", {"filter": f"moment>={YEAR_START}"})
    log.info(f"  OLD paymentin: {len(old_payments)}")

    # Новые платежи 2026
    log.info("Загружаю paymentin NEW 2026...")
    new_payments = get_all(new_s, "entity/paymentin", {"filter": f"moment>={YEAR_START}"})
    log.info(f"  NEW paymentin: {len(new_payments)}")

    # Индекс новых по externalCode и по uuid
    new_by_ext = {}
    new_ops_by_uuid = {}
    for p in new_payments:
        ext = p.get("externalCode")
        new_uuid = uid(p["meta"]["href"])
        if ext:
            new_by_ext[ext] = new_uuid
        new_ops_by_uuid[new_uuid] = p.get("operations", [])

    log.info(f"  Новых с externalCode: {len(new_by_ext)}")

    return old_payments, new_by_ext, new_ops_by_uuid, old_states_by_uuid, new_states_by_name


# ── Фаза 2: чистка карты ─────────────────────────────────────────────────────

def clean_map(umap: UMap, new_by_ext: dict):
    log.info("=== ФАЗА 2: ЧИСТКА КАРТЫ ===")
    old_count = len(umap.data.get("paymentin", {}))
    umap.data["paymentin"] = dict(new_by_ext)  # только верные 953
    umap.save()
    log.info(f"  Карта paymentin: {old_count} → {len(new_by_ext)} записей (коллизии сброшены)")


# ── Фаза 3: создание пропавших ────────────────────────────────────────────────

def create_missing(
    old_s, new_s, umap: UMap,
    old_payments: list, new_by_ext: dict, new_ops_by_uuid: dict,
    old_states_by_uuid: dict, new_states_by_name: dict,
    smoke_limit: int = 0,
):
    log.info("=== ФАЗА 3: СОЗДАНИЕ ПРОПАВШИХ ПЛАТЕЖЕЙ ===")

    missing = [p for p in old_payments if uid(p["meta"]["href"]) not in new_by_ext]
    log.info(f"  Пропавших: {len(missing)}")

    if smoke_limit:
        missing = missing[:smoke_limit]
        log.info(f"  SMOKE режим: создаём только первые {smoke_limit}")

    created = skipped_no_ref = err_count = 0
    skipped_list = []

    for i, old in enumerate(missing):
        old_uuid = uid(old["meta"]["href"])

        # Идемпотентность: если уже создан ранее (например при перезапуске)
        if umap.get("paymentin", old_uuid):
            continue

        # Организация (обязательна)
        org_href = (old.get("organization") or {}).get("meta", {}).get("href", "")
        new_org = umap.get("organization", uid(org_href))
        if not new_org:
            log.warning(f"  ✗ {old.get('name')}: organization не смаплена ({uid(org_href)})")
            skipped_no_ref += 1
            skipped_list.append({"name": old.get("name"), "reason": "no_organization",
                                  "old_uuid": old_uuid})
            continue

        # Агент (обязателен)
        agent_href = (old.get("agent") or {}).get("meta", {}).get("href", "")
        agent_type = agent_href.split("/entity/")[-1].split("/")[0] if "/entity/" in agent_href else ""
        new_agent_uuid = None
        if agent_type == "counterparty":
            new_agent_uuid = umap.get("counterparty", uid(agent_href))
        elif agent_type == "organization":
            new_agent_uuid = umap.get("organization", uid(agent_href))
        if not new_agent_uuid:
            log.warning(f"  ✗ {old.get('name')}: agent не смаплен (type={agent_type}, uuid={uid(agent_href)})")
            skipped_no_ref += 1
            skipped_list.append({"name": old.get("name"), "reason": "no_agent",
                                  "old_uuid": old_uuid})
            continue

        # Тело платежа
        body = {
            "moment": old.get("moment"),
            "sum": old.get("sum", 0),
            "externalCode": old_uuid,
            "applicable": old.get("applicable", True),
            "organization": meta_obj("organization", new_org)["meta"],
            "agent": meta_obj(agent_type, new_agent_uuid)["meta"],
        }
        # Правильный формат: organization/agent как meta-объекты
        body["organization"] = {"meta": {
            "href": f"{BASE}/entity/organization/{new_org}",
            "type": "organization", "mediaType": "application/json"}}
        body["agent"] = {"meta": {
            "href": f"{BASE}/entity/{agent_type}/{new_agent_uuid}",
            "type": agent_type, "mediaType": "application/json"}}

        # Необязательные поля
        for f in ("name", "incomingNumber", "incomingDate", "paymentPurpose",
                  "description", "vatSum", "payedSum"):
            if old.get(f) is not None:
                body[f] = old[f]

        # Статус
        state_href = (old.get("state") or {}).get("meta", {}).get("href", "")
        if state_href:
            old_state = old_states_by_uuid.get(uid(state_href), {})
            state_name = old_state.get("name") if old_state else None
            if state_name:
                new_state_uuid = new_states_by_name.get(state_name)
                if new_state_uuid:
                    body["state"] = {"meta": {
                        "href": f"{BASE}/entity/paymentin/metadata/states/{new_state_uuid}",
                        "type": "state", "mediaType": "application/json"}}

        # Операции (связки с документами)
        ops = _build_operations(old.get("operations", []), umap)
        if ops:
            body["operations"] = ops

        # POST
        r = api_req(new_s, "POST", f"{BASE}/entity/paymentin", json=body)
        if r.ok:
            new_uuid = uid(r.json()["meta"]["href"])
            umap.set("paymentin", old_uuid, new_uuid)
            new_by_ext[old_uuid] = new_uuid
            new_ops_by_uuid[new_uuid] = r.json().get("operations", ops)
            created += 1
            if created % 50 == 0:
                umap.save()
                log.info(f"  Создано: {created}/{len(missing)}")
        else:
            err_text = r.text
            # 3006 + externalCode — уже создан
            if "3006" in err_text and "externalCode" in err_text:
                r2 = api_req(new_s, "GET", f"{BASE}/entity/paymentin",
                             params={"filter": f"externalCode={old_uuid}", "limit": 1})
                rows = r2.json().get("rows", []) if r2.ok else []
                if rows:
                    new_uuid = uid(rows[0]["meta"]["href"])
                    umap.set("paymentin", old_uuid, new_uuid)
                    new_by_ext[old_uuid] = new_uuid
                    new_ops_by_uuid[new_uuid] = rows[0].get("operations", [])
                    created += 1
                    continue
            # 3006 + name — конфликт номера между организациями
            if "3006" in err_text and "name" in err_text:
                body.pop("name", None)
                r2 = api_req(new_s, "POST", f"{BASE}/entity/paymentin", json=body)
                if r2.ok:
                    new_uuid = uid(r2.json()["meta"]["href"])
                    umap.set("paymentin", old_uuid, new_uuid)
                    new_by_ext[old_uuid] = new_uuid
                    new_ops_by_uuid[new_uuid] = r2.json().get("operations", ops)
                    created += 1
                    log.info(f"  + {old.get('name')} (без name, конфликт номера)")
                    continue
            log.warning(f"  ✗ {old.get('name')} ({old_uuid}): {err_text[:200]}")
            err_count += 1

    umap.save()
    log.info(f"  Создано: {created}, пропущено (нет ссылок): {skipped_no_ref}, ошибок: {err_count}")
    return skipped_list, created, skipped_no_ref, err_count


def _build_operations(old_ops: list, umap: UMap) -> list:
    """Смапить операции старого платежа в ссылки нового аккаунта."""
    result = []
    for op in old_ops:
        op_href = op.get("meta", {}).get("href", "")
        op_type = op_href.split("/entity/")[-1].split("/")[0] if "/entity/" in op_href else ""
        op_old_uuid = uid(op_href)
        if not op_type or not op_old_uuid:
            continue
        op_new_uuid = umap.get(op_type, op_old_uuid)
        if not op_new_uuid:
            continue
        entry = {"meta": {
            "href": f"{BASE}/entity/{op_type}/{op_new_uuid}",
            "type": op_type, "mediaType": "application/json"}}
        if op.get("linkedSum") is not None:
            entry["linkedSum"] = op["linkedSum"]
        result.append(entry)
    return result


# ── Фаза 4: перепроверка связей ───────────────────────────────────────────────

def relink_all(
    old_s, new_s, umap: UMap,
    old_payments: list, new_ops_by_uuid: dict,
):
    log.info("=== ФАЗА 4: ПЕРЕПРОВЕРКА СВЯЗЕЙ ВСЕХ ПЛАТЕЖЕЙ ===")

    relinked = already_ok = no_ops_in_old = skipped_no_map = op_unmapped = 0
    put_count = 0

    for i, old in enumerate(old_payments):
        old_uuid = uid(old["meta"]["href"])
        new_uuid = umap.get("paymentin", old_uuid)
        if not new_uuid:
            skipped_no_map += 1
            continue

        old_ops = old.get("operations", [])
        if not old_ops:
            no_ops_in_old += 1
            continue

        desired = _build_operations(old_ops, umap)
        unmapped_count = len(old_ops) - len(desired)
        if unmapped_count:
            op_unmapped += unmapped_count

        if not desired:
            no_ops_in_old += 1
            continue

        # Текущие операции нового платежа
        current_ops = new_ops_by_uuid.get(new_uuid, [])
        current_set = _ops_set(current_ops)
        desired_set = _ops_set(desired)

        if current_set == desired_set:
            already_ok += 1
            continue

        # Нужен PUT
        r = api_req(new_s, "PUT", f"{BASE}/entity/paymentin/{new_uuid}",
                    json={"operations": desired})
        put_count += 1
        # Лимит ~100 PUT/мин на сущность — небольшая пауза
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... поставлено {relinked}")
            time.sleep(62)

        if r.ok:
            new_ops_by_uuid[new_uuid] = r.json().get("operations", desired)
            relinked += 1
            if relinked % 100 == 0:
                log.info(f"  Переставлено связей: {relinked}")
        else:
            log.warning(f"  ✗ PUT paymentin/{new_uuid}: {r.text[:200]}")

    log.info(f"  Переставлено: {relinked}, уже_совпадало: {already_ok}, "
             f"нет_ops_в_old: {no_ops_in_old}, нет_в_карте: {skipped_no_map}, "
             f"немапленных_операций: {op_unmapped}")
    return relinked, already_ok, no_ops_in_old, skipped_no_map, op_unmapped


def _ops_set(ops: list) -> frozenset:
    """Канонический fingerprint набора операций для сравнения."""
    result = set()
    for op in ops:
        href = op.get("meta", {}).get("href", "") if isinstance(op, dict) else ""
        op_uuid = uid(href)
        linked = round(op.get("linkedSum", 0)) if isinstance(op, dict) else 0
        if op_uuid:
            result.add((op_uuid, linked))
    return frozenset(result)


# ── Фаза 5: отчёт ────────────────────────────────────────────────────────────

def final_report(
    old_s, new_s,
    old_count: int, created: int, skipped_no_ref: int, err_count: int,
    relinked: int, already_ok: int, no_ops: int, no_map: int, op_unmapped: int,
    skipped_list: list,
):
    log.info("=== ФАЗА 5: ФИНАЛЬНЫЙ ОТЧЁТ ===")

    # Актуальный счётчик
    r_old = api_req(old_s, "GET", f"{BASE}/entity/paymentin",
                    params={"limit": 1, "filter": f"moment>={YEAR_START}"})
    r_new = api_req(new_s, "GET", f"{BASE}/entity/paymentin",
                    params={"limit": 1, "filter": f"moment>={YEAR_START}"})
    final_old = r_old.json().get("meta", {}).get("size", "?")
    final_new = r_new.json().get("meta", {}).get("size", "?")
    match = "OK" if final_old == final_new else "DIFF"

    report = {
        "counts": {"old_2026": final_old, "new_2026": final_new, "match": match},
        "phase3": {"created": created, "skipped_no_ref": skipped_no_ref, "errors": err_count},
        "phase4": {
            "relinked": relinked, "already_ok": already_ok,
            "no_ops_in_old": no_ops, "skipped_no_map": no_map,
            "op_unmapped_count": op_unmapped,
        },
        "skipped_docs": skipped_list,
    }

    out = SCRIPT_DIR / "payments_fix_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"\n{'='*60}")
    log.info(f"paymentin 2026: OLD={final_old} NEW={final_new} [{match}]")
    log.info(f"Создано: {created} | Пропущено (нет ссылок): {skipped_no_ref} | Ошибок: {err_count}")
    log.info(f"Связки — переставлено: {relinked} | уже_ок: {already_ok} | "
             f"без_операций: {no_ops} | не_в_карте: {no_map} | немапленных_ops: {op_unmapped}")
    log.info(f"Отчёт: {out}")
    log.info(f"{'='*60}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", type=int, default=0,
                        help="Пробный прогон: создать только первые N пропавших платежей")
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = UMap()

    # Фаза 1
    old_payments, new_by_ext, new_ops_by_uuid, old_states_by_uuid, new_states_by_name = \
        load_data(old_s, new_s)

    # Фаза 2
    clean_map(umap, new_by_ext)

    # Фаза 3
    skipped_list, created, skipped_no_ref, err_count = create_missing(
        old_s, new_s, umap,
        old_payments, new_by_ext, new_ops_by_uuid,
        old_states_by_uuid, new_states_by_name,
        smoke_limit=args.smoke,
    )

    if args.smoke:
        log.info(f"SMOKE завершён: создано={created}. Для полного прогона запустите без --smoke.")
        return

    # Фаза 4
    relinked, already_ok, no_ops, no_map, op_unmapped = relink_all(
        old_s, new_s, umap, old_payments, new_ops_by_uuid
    )

    # Фаза 5
    final_report(
        old_s, new_s,
        len(old_payments), created, skipped_no_ref, err_count,
        relinked, already_ok, no_ops, no_map, op_unmapped,
        skipped_list,
    )


if __name__ == "__main__":
    main()
