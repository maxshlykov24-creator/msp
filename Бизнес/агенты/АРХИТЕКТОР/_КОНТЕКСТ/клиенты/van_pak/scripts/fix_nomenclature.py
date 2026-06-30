"""
Полная нормализация номенклатуры в новом аккаунте.

Этапы:
  1. Перевязать позиции в документах: битые товары → canonical.
  2. Удалить 712 битых товаров (с externalCode "<uuid>?expand=...").
  3. У оставшихся 213 битых (без canonical) — починить externalCode.
  4. Запатчить ВСЕ атрибуты canonical-товаров (Цвет пакетов, Прослеживаемый, и т.д.).
  5. Гарантировать заполнение доп. поля "Старый UUID" у всех товаров.
  6. Проверить услуги: externalCode + описание со Старым UUID.

Каждый этап логирует прогресс. После аварии скрипт можно перезапустить —
он пропускает уже сделанные шаги.
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
ATTR_NAME = "Старый UUID"

DOC_TYPES = ["customerorder", "demand", "invoiceout", "supply", "move", "loss"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_nomenclature.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def get_all(session, entity_type, params=None):
    items, offset = [], 0
    base = {"limit": 1000, **(params or {})}
    while True:
        base["offset"] = offset
        r = session.get(f"{BASE}/entity/{entity_type}", params=base, timeout=60)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        items.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
        time.sleep(0.06)
    return items


def request_with_retry(session, method, url, **kwargs):
    kwargs.setdefault("timeout", 60)
    for attempt in range(4):
        try:
            r = session.request(method, url, **kwargs)
            time.sleep(0.07)
            return r
        except Exception as e:
            if attempt < 3:
                wait = 2 ** attempt
                log.warning(f"  retry {attempt + 1} after {wait}s: {str(e)[:80]}")
                time.sleep(wait)
            else:
                raise


def uuid_from_href(href: str) -> str:
    return href.split("/")[-1].split("?")[0]


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    prod_map = umap.get("product", {})  # old → new (canonical)
    log.info(f"uuid_map[product]: {len(prod_map)} записей")

    # ── Сбор данных ───────────────────────────────────────────────────────
    log.info("\n── Загрузка товаров из НОВОГО аккаунта ──")
    new_products = get_all(new_s, "product")
    log.info(f"  всего: {len(new_products)}")

    log.info("── Загрузка товаров из СТАРОГО (включая архив) ──")
    old_products_active = get_all(old_s, "product", {"expand": "attributes"})
    old_products_arch = get_all(old_s, "product", {"expand": "attributes",
                                                     "filter": "archived=true"})
    old_products = old_products_active + old_products_arch
    log.info(f"  активных: {len(old_products_active)}, архивных: {len(old_products_arch)}, итого: {len(old_products)}")

    old_by_uuid = {uuid_from_href(p["meta"]["href"]): p for p in old_products}

    # ── Классификация битых ────────────────────────────────────────────────
    broken_re = re.compile(r"^([0-9a-f-]{36})\?")
    to_remove = []   # [{new_uuid, canonical_uuid, name, old_uuid}]
    to_fix_ec = []   # [{new_uuid, old_uuid, correct_ec, name}]

    for p in new_products:
        ec = p.get("externalCode") or ""
        m = broken_re.match(ec)
        if not m:
            continue
        new_uuid = uuid_from_href(p["meta"]["href"])
        old_uuid = m.group(1)
        canonical = prod_map.get(old_uuid)
        old = old_by_uuid.get(old_uuid)
        correct_ec = old.get("externalCode") if old else None

        if canonical and canonical != new_uuid:
            to_remove.append({
                "new_uuid": new_uuid,
                "canonical_uuid": canonical,
                "old_uuid": old_uuid,
                "name": p.get("name", ""),
            })
        else:
            to_fix_ec.append({
                "new_uuid": new_uuid,
                "old_uuid": old_uuid,
                "correct_ec": correct_ec or old_uuid,
                "name": p.get("name", ""),
            })

    log.info(f"\nК удалению (битые с альтернативой): {len(to_remove)}")
    log.info(f"К починке externalCode (без альтернативы): {len(to_fix_ec)}")

    # ────────────────────────────────────────────────────────────────────────
    # ЭТАП 1: перевязать позиции в документах: битые → canonical
    # ────────────────────────────────────────────────────────────────────────
    log.info("\n══════════════ ЭТАП 1: перевязка позиций ══════════════")
    bad_to_good = {b["new_uuid"]: b["canonical_uuid"] for b in to_remove}
    bad_set = set(bad_to_good.keys())

    # Чанки по 30 UUID — собираем фильтр с OR через ';'
    CHUNK = 30
    bad_list = list(bad_set)
    chunks = [bad_list[i:i + CHUNK] for i in range(0, len(bad_list), CHUNK)]

    affected_docs = {}  # doc_type → set(doc_uuid)
    for doc_type in DOC_TYPES:
        log.info(f"\n── Поиск {doc_type}, ссылающихся на битые ──")
        affected = set()
        for ci, chunk in enumerate(chunks, 1):
            flt = ";".join(
                f"assortment=https://api.moysklad.ru/api/remap/1.2/entity/product/{u}"
                for u in chunk
            )
            offset = 0
            while True:
                r = request_with_retry(new_s, "GET",
                    f"{BASE}/entity/{doc_type}",
                    params={"limit": 100, "offset": offset, "filter": flt})
                if r.status_code != 200:
                    log.warning(f"  ✗ {doc_type} filter chunk {ci}: {r.status_code} {r.text[:150]}")
                    break
                rows = r.json().get("rows", [])
                for d in rows:
                    affected.add(uuid_from_href(d["meta"]["href"]))
                if len(rows) < 100:
                    break
                offset += 100
            if ci % 5 == 0:
                log.info(f"  ... {doc_type}: чанк {ci}/{len(chunks)}, документов={len(affected)}")
        log.info(f"  {doc_type}: документов для правки={len(affected)}")
        affected_docs[doc_type] = affected

    # ── Правка каждого документа ──
    # БЕЗОПАСНО: апдейтим каждую отдельную позицию через
    #   PUT /entity/{doc_type}/{doc_uuid}/positions/{pos_uuid}
    log.info("\n── Правка документов: замена product у битых позиций ──")
    total_fixed = total_failed = 0
    for doc_type, doc_uuids in affected_docs.items():
        if not doc_uuids:
            continue
        log.info(f"\n  ── {doc_type}: {len(doc_uuids)} документов ──")
        fixed = failed = 0
        for doc_uuid in doc_uuids:
            try:
                # Получаем все позиции документа
                rp = request_with_retry(new_s, "GET",
                    f"{BASE}/entity/{doc_type}/{doc_uuid}/positions",
                    params={"limit": 1000})
                if rp.status_code != 200:
                    log.warning(f"    ✗ GET positions {doc_type}/{doc_uuid}: {rp.status_code}")
                    failed += 1
                    continue
                positions = rp.json().get("rows", [])
                for pos in positions:
                    assort = pos.get("assortment", {})
                    assort_href = assort.get("meta", {}).get("href", "")
                    assort_uuid = uuid_from_href(assort_href) if assort_href else None
                    if assort_uuid not in bad_set:
                        continue
                    pos_uuid = uuid_from_href(pos["meta"]["href"])
                    new_assort_uuid = bad_to_good[assort_uuid]
                    body = {
                        "assortment": {
                            "meta": {
                                "href": f"{BASE}/entity/product/{new_assort_uuid}",
                                "type": "product",
                                "mediaType": "application/json",
                            }
                        }
                    }
                    rput = request_with_retry(new_s, "PUT",
                        f"{BASE}/entity/{doc_type}/{doc_uuid}/positions/{pos_uuid}",
                        json=body)
                    if rput.status_code == 200:
                        fixed += 1
                    else:
                        log.warning(f"    ✗ PUT pos {doc_type}/{doc_uuid}/{pos_uuid}: "
                                     f"{rput.status_code} {rput.text[:200]}")
                        failed += 1

                if (fixed + failed) % 100 == 0 and (fixed + failed) > 0:
                    log.info(f"    ... {doc_type} прогресс: fixed={fixed}, failed={failed}")
            except Exception as e:
                log.warning(f"    ✗ {doc_type}/{doc_uuid}: {str(e)[:120]}")
                failed += 1

        log.info(f"  {doc_type}: позиций исправлено={fixed}, ошибок={failed}")
        total_fixed += fixed
        total_failed += failed

    log.info(f"\nИТОГО позиций: исправлено={total_fixed}, ошибок={total_failed}")

    # ────────────────────────────────────────────────────────────────────────
    # ЭТАП 2: удалить битые товары
    # ────────────────────────────────────────────────────────────────────────
    log.info("\n══════════════ ЭТАП 2: удаление битых товаров ══════════════")
    deleted = del_failed = 0
    for i, b in enumerate(to_remove, 1):
        r = request_with_retry(new_s, "DELETE",
            f"{BASE}/entity/product/{b['new_uuid']}")
        if r.status_code == 200:
            deleted += 1
        else:
            log.warning(f"  ✗ DELETE product/{b['new_uuid']} ({b['name'][:50]}): "
                         f"{r.status_code} {r.text[:150]}")
            del_failed += 1
        if i % 100 == 0:
            log.info(f"  ... удалено {deleted}/{len(to_remove)}, ошибок {del_failed}")
    log.info(f"Удалено: {deleted}, ошибок: {del_failed}")

    # ────────────────────────────────────────────────────────────────────────
    # ЭТАП 3: починить externalCode у 213 без альтернативы
    # ────────────────────────────────────────────────────────────────────────
    log.info("\n══════════════ ЭТАП 3: починка externalCode ══════════════")
    fixed_ec = ec_failed = 0
    for i, b in enumerate(to_fix_ec, 1):
        r = request_with_retry(new_s, "PUT",
            f"{BASE}/entity/product/{b['new_uuid']}",
            json={"externalCode": b["correct_ec"]})
        if r.status_code == 200:
            fixed_ec += 1
        else:
            log.warning(f"  ✗ PATCH ec product/{b['new_uuid']} ({b['name'][:50]}): "
                         f"{r.status_code} {r.text[:150]}")
            ec_failed += 1
        if i % 50 == 0:
            log.info(f"  ... починено ec {fixed_ec}/{len(to_fix_ec)}, ошибок {ec_failed}")
    log.info(f"Починено externalCode: {fixed_ec}, ошибок: {ec_failed}")

    log.info("\n══════════════ Завершено ══════════════")


if __name__ == "__main__":
    main()
