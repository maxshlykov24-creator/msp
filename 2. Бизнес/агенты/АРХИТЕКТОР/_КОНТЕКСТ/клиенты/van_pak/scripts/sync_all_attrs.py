"""
Полная синхронизация атрибутов/полей товаров и услуг со старого аккаунта.

Для каждого товара/услуги в новом, у которого старый партнёр найден через uuid_map:
  - description, weight, volume, vat, vatEnabled, article
  - salePrices, buyPrice, minPrice
  - trackingType
  - все custom attributes (Цвет пакетов, Прослеживаемый, Постоянная складская позиция, и т.д.)
  - доп. поле "Старый UUID" — гарантированно заполнено

Для услуг дополнительно:
  - description с пометкой [Старый UUID: ...]
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
ATTR_OLD_UUID = "Старый UUID"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "sync_all_attrs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def request_with_retry(session, method, url, **kwargs):
    kwargs.setdefault("timeout", 60)
    for attempt in range(4):
        try:
            r = session.request(method, url, **kwargs)
            time.sleep(0.07)
            return r
        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
            else:
                raise


def get_all(session, entity_type, params=None):
    items, offset = [], 0
    base = {"limit": 1000, **(params or {})}
    while True:
        base["offset"] = offset
        r = request_with_retry(session, "GET",
            f"{BASE}/entity/{entity_type}", params=base)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        items.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return items


def uuid_from_href(href: str) -> str:
    return href.split("/")[-1].split("?")[0]


def find_or_create_old_uuid_attr(session, entity_kind: str):
    """entity_kind: 'product' (для product/service общий)"""
    r = request_with_retry(session, "GET",
        f"{BASE}/entity/{entity_kind}/metadata/attributes")
    r.raise_for_status()
    for a in r.json().get("rows", []):
        if a.get("name") == ATTR_OLD_UUID:
            return a["meta"]
    # создаём
    body = {"name": ATTR_OLD_UUID, "type": "string", "required": False}
    r2 = request_with_retry(session, "POST",
        f"{BASE}/entity/{entity_kind}/metadata/attributes", json=body)
    r2.raise_for_status()
    return r2.json()["meta"]


def map_attributes(old_attrs, attr_map, ce_val_map):
    """
    old_attrs: list атрибутов из старого товара
    attr_map: name → meta атрибута в новом аккаунте
    ce_val_map: (dict_uuid, value_uuid) → new value meta
    """
    result = []
    for a in old_attrs or []:
        name = a.get("name")
        new_meta = attr_map.get(name)
        if not new_meta:
            continue
        val = a.get("value")
        if val is None:
            continue
        atype = a.get("type")
        if atype == "customentity" and isinstance(val, dict):
            href = val.get("meta", {}).get("href", "")
            parts = href.replace(f"{BASE}/", "").split("/")
            # entity/customentity/{dict_uuid}/{value_uuid}
            if len(parts) >= 4:
                dict_uuid = parts[2]
                val_uuid = parts[3].split("?")[0]
                new_val_meta = ce_val_map.get((dict_uuid, val_uuid))
                if new_val_meta:
                    result.append({"meta": new_meta, "value": {"meta": new_val_meta}})
        elif isinstance(val, (str, int, float, bool)):
            result.append({"meta": new_meta, "value": val})
        elif isinstance(val, dict) and "name" in val:
            # employee, contract — пропускаем (нужны мэппинги, сложно)
            continue
    return result


def main():
    old_s = requests.Session()
    old_s.headers.update({"Authorization": f"Bearer {OLD_TOKEN}", "Accept-Encoding": "gzip"})
    new_s = requests.Session()
    new_s.headers.update({"Authorization": f"Bearer {NEW_TOKEN}", "Accept-Encoding": "gzip"})

    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    log.info(f"uuid_map[product]={len(umap.get('product',{}))}, "
             f"[service]={len(umap.get('service',{}))}")

    # ── Получаем дефолтную валюту и priceType в НОВОМ аккаунте ──
    r_curr = request_with_retry(new_s, "GET", f"{BASE}/entity/currency")
    new_currency_meta = None
    for c in r_curr.json().get("rows", []):
        if c.get("isoCode") == "RUB" or c.get("default"):
            new_currency_meta = c["meta"]
            break
    if not new_currency_meta:
        rows = r_curr.json().get("rows", [])
        new_currency_meta = rows[0]["meta"] if rows else None

    r_pt = request_with_retry(new_s, "GET",
        f"{BASE}/context/companysettings/pricetype")
    pt_rows = r_pt.json()
    new_pricetype_meta = pt_rows[0]["meta"] if isinstance(pt_rows, list) and pt_rows else None

    log.info(f"  currency: {new_currency_meta['href'].split('/')[-1] if new_currency_meta else None}")
    log.info(f"  pricetype: {new_pricetype_meta['href'].split('/')[-1] if new_pricetype_meta else None}")

    # ── Маппинг атрибутов товаров (name → new meta) ──
    log.info("\n── Маппинг атрибутов product/service ──")
    r_attrs_new = request_with_retry(new_s, "GET",
        f"{BASE}/entity/product/metadata/attributes")
    new_attr_map = {a["name"]: a["meta"] for a in r_attrs_new.json().get("rows", [])}
    log.info(f"  атрибутов в новом: {len(new_attr_map)}")
    for nm in new_attr_map:
        log.info(f"    • {nm}")

    # Убедимся, что есть "Старый UUID"
    if ATTR_OLD_UUID not in new_attr_map:
        log.info(f"  Создаю атрибут «{ATTR_OLD_UUID}»")
        meta = find_or_create_old_uuid_attr(new_s, "product")
        new_attr_map[ATTR_OLD_UUID] = meta
    old_uuid_attr_meta = new_attr_map[ATTR_OLD_UUID]

    # ── Маппинг значений customentity ──
    log.info("\n── Маппинг customentity-значений ──")
    ce_dicts_old = umap.get("customentity_dict", {})  # old → new
    ce_vals_old = umap.get("customentity_value", {})  # old_val_uuid → new_val_uuid

    # Строим карту: (old_dict_uuid, old_val_uuid) → new value meta
    ce_val_map = {}
    for old_dict_uuid, new_dict_uuid in ce_dicts_old.items():
        # Подгрузим значения нового справочника один раз
        try:
            rcd = request_with_retry(new_s, "GET",
                f"{BASE}/entity/customentity/{new_dict_uuid}", params={"limit": 1000})
            for v in rcd.json().get("rows", []):
                new_val_uuid = uuid_from_href(v["meta"]["href"])
                # Ищем в маппинге какой old_val_uuid сюда указывает
                for old_val, new_val in ce_vals_old.items():
                    if new_val == new_val_uuid:
                        ce_val_map[(old_dict_uuid, old_val)] = v["meta"]
        except Exception as e:
            log.warning(f"  ✗ загрузка values для {new_dict_uuid}: {e}")

    log.info(f"  пар (dict_uuid, val_uuid) → meta: {len(ce_val_map)}")

    # ── Старые товары (включая архивные) с expand ──
    log.info("\n── Загрузка старых товаров с атрибутами ──")
    old_active = get_all(old_s, "product", {"expand": "attributes"})
    old_arch = get_all(old_s, "product", {"expand": "attributes", "filter": "archived=true"})
    old_products = old_active + old_arch
    log.info(f"  активных: {len(old_active)}, архивных: {len(old_arch)}")
    old_by_uuid = {uuid_from_href(p["meta"]["href"]): p for p in old_products}

    log.info("── Загрузка старых услуг ──")
    old_services = get_all(old_s, "service", {"expand": "attributes"})
    old_arch_svc = get_all(old_s, "service", {"expand": "attributes", "filter": "archived=true"})
    old_services += old_arch_svc
    log.info(f"  всего услуг src: {len(old_services)}")
    old_svc_by_uuid = {uuid_from_href(s["meta"]["href"]): s for s in old_services}

    # ── Синхронизация товаров ──
    log.info("\n══════════════ Синхронизация товаров ══════════════")
    prod_map = umap.get("product", {})
    updated = skipped = failed = 0
    for i, (old_uuid, new_uuid) in enumerate(prod_map.items(), 1):
        old_p = old_by_uuid.get(old_uuid)
        if not old_p:
            skipped += 1
            continue

        # Собираем body
        body = {}
        for fld in ("description", "weight", "volume", "vat", "vatEnabled",
                    "article", "trackingType"):
            v = old_p.get(fld)
            if v is not None:
                body[fld] = v

        if old_p.get("minPrice") and isinstance(old_p["minPrice"], dict):
            body["minPrice"] = {
                "value": old_p["minPrice"].get("value", 0),
                "currency": {"meta": new_currency_meta},
            }
        if old_p.get("buyPrice") and isinstance(old_p["buyPrice"], dict):
            body["buyPrice"] = {
                "value": old_p["buyPrice"].get("value", 0),
                "currency": {"meta": new_currency_meta},
            }
        if old_p.get("salePrices"):
            sps = []
            for sp in old_p["salePrices"]:
                new_sp = {
                    "value": sp.get("value", 0),
                    "currency": {"meta": new_currency_meta},
                }
                if new_pricetype_meta:
                    new_sp["priceType"] = {"meta": new_pricetype_meta}
                sps.append(new_sp)
            body["salePrices"] = sps

        # Архив
        if old_p.get("archived"):
            body["archived"] = True

        # Атрибуты
        mapped = map_attributes(old_p.get("attributes", []), new_attr_map, ce_val_map)
        # Гарантируем "Старый UUID"
        has_old = any(a["meta"]["href"] == old_uuid_attr_meta["href"] for a in mapped)
        if not has_old:
            mapped.append({"meta": old_uuid_attr_meta, "value": old_uuid})
        body["attributes"] = mapped

        r = request_with_retry(new_s, "PUT",
            f"{BASE}/entity/product/{new_uuid}", json=body)
        if r.status_code == 200:
            updated += 1
        else:
            log.warning(f"  ✗ product/{new_uuid} ({old_p.get('name','?')[:50]}): "
                         f"{r.status_code} {r.text[:200]}")
            failed += 1

        if i % 100 == 0:
            log.info(f"  товары: {i}/{len(prod_map)} (обн={updated}, проп={skipped}, ош={failed})")

    log.info(f"Товары: обновлено={updated}, пропущено={skipped}, ошибок={failed}")

    # ── Синхронизация услуг ──
    log.info("\n══════════════ Синхронизация услуг ══════════════")
    svc_map = umap.get("service", {})
    s_updated = s_skipped = s_failed = 0
    for i, (old_uuid, new_uuid) in enumerate(svc_map.items(), 1):
        old_s_item = old_svc_by_uuid.get(old_uuid)
        if not old_s_item:
            s_skipped += 1
            continue

        body = {}
        orig_desc = old_s_item.get("description", "") or ""
        tag = f"[Старый UUID: {old_uuid}]"
        if tag not in orig_desc:
            new_desc = f"{orig_desc}\n{tag}".strip()
        else:
            new_desc = orig_desc
        body["description"] = new_desc

        for fld in ("weight", "volume", "vat", "vatEnabled", "article"):
            v = old_s_item.get(fld)
            if v is not None:
                body[fld] = v
        if old_s_item.get("minPrice") and isinstance(old_s_item["minPrice"], dict):
            body["minPrice"] = {
                "value": old_s_item["minPrice"].get("value", 0),
                "currency": {"meta": new_currency_meta},
            }
        if old_s_item.get("buyPrice") and isinstance(old_s_item["buyPrice"], dict):
            body["buyPrice"] = {
                "value": old_s_item["buyPrice"].get("value", 0),
                "currency": {"meta": new_currency_meta},
            }
        if old_s_item.get("salePrices"):
            sps = []
            for sp in old_s_item["salePrices"]:
                new_sp = {
                    "value": sp.get("value", 0),
                    "currency": {"meta": new_currency_meta},
                }
                if new_pricetype_meta:
                    new_sp["priceType"] = {"meta": new_pricetype_meta}
                sps.append(new_sp)
            body["salePrices"] = sps
        if old_s_item.get("archived"):
            body["archived"] = True

        # ec — оригинальный
        orig_ec = old_s_item.get("externalCode")
        if orig_ec:
            body["externalCode"] = orig_ec

        r = request_with_retry(new_s, "PUT",
            f"{BASE}/entity/service/{new_uuid}", json=body)
        if r.status_code == 200:
            s_updated += 1
        else:
            log.warning(f"  ✗ service/{new_uuid} ({old_s_item.get('name','?')[:50]}): "
                         f"{r.status_code} {r.text[:200]}")
            s_failed += 1

    log.info(f"Услуги: обновлено={s_updated}, пропущено={s_skipped}, ошибок={s_failed}")

    log.info("\nЗавершено.")


if __name__ == "__main__":
    main()
