"""
МойСклад → МойСклад: миграция аккаунта Wangpack (ВАН ПАК), v2
==============================================================
Запуск:
    PILOT (проверка):
        python migrate.py --pilot

    FULL (после успешной проверки pilot):
        python migrate.py

Переменные окружения (опционально, можно переопределить токены):
    OLD_TOKEN, NEW_TOKEN

Что делает (в правильном порядке зависимостей):
    1.  Справочники customentity (Канал продаж, Способ оплаты, и т.д.)
    2.  Кастомные атрибуты документов (с customEntityMeta для customentity-типа)
    3.  Организации
    4.  Единицы измерения (UOM)
    5.  Склады
    6.  Папки номенклатуры (иерархия)
    7.  Товары + Услуги
    8.  Контрагенты
    9.  Ввод остатков на 01.01.2026 (асинхронный отчёт)
    10. Документы 2026: Приёмки → Заказы → Отгрузки → Счета → Платежи → Перемещения → Списания
    11. Связи платежей с заказами/счетами (paymentin.operations)
    12. Верификация: счётчики

Логика pilot:
    --pilot ограничивает выборки: 10 товаров, 5 услуг, 10 контрагентов,
    по 3 документа каждого типа. Справочники переносятся полностью.

Идемпотентность:
    uuid_map.json — маппинг old_uuid → new_uuid, сохраняется после КАЖДОЙ операции.
    Повторный запуск пропускает уже перенесённые сущности.
    externalCode = old_uuid — fallback при потере маппинга.

Фиксы по документации МойСклад API:
    - rate limit 20 req/sec (sleep 0.05с)
    - batch POST до 1000 объектов (документы — по 100 из-за payload)
    - expand=100 limit (а не 1000)
    - async-режим для /report/stock/all
    - customEntityMeta для атрибутов типа customentity
"""

import argparse
import json
import logging
import os
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ── Конфигурация ─────────────────────────────────────────────────────────────

OLD_TOKEN = os.environ.get("OLD_TOKEN", "e95af8f2c93487a215f754ea1e5469e281613d61")
NEW_TOKEN = os.environ.get("NEW_TOKEN", "619a40e860cb6ad975c3d05cae7157b29caccc0e")

BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
LOG_FILE = SCRIPT_DIR / "migrate.log"

YEAR_START = "2026-01-01 00:00:00"
STOCK_MOMENT = "2025-12-31 23:59:59"

# Rate limit: 100 req / 5 sec = 20 req/sec. С запасом — 0.06с между запросами.
REQUEST_SLEEP = 0.06

# Лимиты выборок в pilot-режиме
PILOT_PRODUCTS = 10
PILOT_SERVICES = 5
PILOT_COUNTERPARTIES = 10
PILOT_DOCS_PER_TYPE = 3

# ── Логирование ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── HTTP клиент ───────────────────────────────────────────────────────────────


class MS:
    """Обёртка над requests для МойСклад JSON API 1.2."""

    def __init__(self, token: str, label: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip",
        })
        self.label = label

    def _req(self, method: str, url: str, **kwargs) -> requests.Response:
        for attempt in range(6):
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)
            except requests.exceptions.RequestException as e:
                log.warning(f"[{self.label}] Сетевая ошибка: {e}, попытка {attempt+1}/6")
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("X-Lognex-Retry-After", 3000)) / 1000
                log.warning(f"[{self.label}] 429 Rate limit, жду {wait}с")
                time.sleep(wait + 0.5)
                continue
            if 500 <= resp.status_code < 600:
                wait = 2 ** attempt
                log.warning(f"[{self.label}] {resp.status_code}, попытка {attempt+1}/6, sleep {wait}с")
                time.sleep(wait)
                continue
            time.sleep(REQUEST_SLEEP)
            return resp
        raise RuntimeError(f"[{self.label}] {method} {url} — провал после 6 попыток")

    def get(self, path: str, params: dict = None) -> dict:
        resp = self._req("GET", f"{BASE}/{path}", params=params)
        if not resp.ok:
            raise RuntimeError(f"GET {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def post(self, path: str, body):
        resp = self._req("POST", f"{BASE}/{path}", json=body)
        if not resp.ok:
            raise RuntimeError(f"POST {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def put(self, path: str, body):
        resp = self._req("PUT", f"{BASE}/{path}", json=body)
        if not resp.ok:
            raise RuntimeError(f"PUT {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def get_all(self, path: str, params: dict = None, expand: str = None) -> list:
        """Пагинированная выгрузка. С expand limit=100, иначе 1000."""
        result = []
        offset = 0
        limit = 100 if expand else 1000
        p = dict(params or {})
        p["limit"] = limit
        if expand:
            p["expand"] = expand
        while True:
            p["offset"] = offset
            data = self.get(path, params=p)
            rows = data.get("rows", [])
            result.extend(rows)
            if len(rows) < limit:
                break
            offset += limit
            if offset % 1000 == 0:
                log.info(f"  [{self.label}] {path}: {offset}+")
        return result

    def async_report(self, path: str, params: dict = None) -> list:
        """
        Асинхронный запрос отчёта.
        GET ?async=true → 202 + Location → polling /async/{id} → GET resultUrl.
        """
        p = dict(params or {})
        p["async"] = "true"
        resp = self._req("GET", f"{BASE}/{path}", params=p)
        if resp.status_code != 202:
            log.warning(f"async ожидался 202, получен {resp.status_code} — fallback на синхронный")
            return self.get_all(path, params=params)
        status_url = resp.headers.get("Content-Location")
        result_url = resp.headers.get("Location")
        log.info(f"  async {path} запущен, опрашиваю {status_url}")
        for _ in range(300):  # до 10 мин
            time.sleep(2)
            r = self._req("GET", status_url)
            if not r.ok:
                continue
            state = r.json().get("state", "")
            if state == "DONE":
                log.info(f"  async DONE, забираю {result_url}")
                r2 = self._req("GET", result_url)
                if r2.ok:
                    data = r2.json()
                    return data.get("rows", []) if isinstance(data, dict) else data
            if state in ("ERROR", "CANCEL", "API_ERROR"):
                raise RuntimeError(f"async задача упала: {state}")
        raise RuntimeError("async задача не завершилась за 10 мин")

    def post_batch(self, path: str, items: list, batch_size: int = 100,
                   on_each_success=None) -> list:
        """
        Batch POST с fallback на одиночные POST при ошибке батча.
        on_each_success(old_index, response_dict) — колбэк для записи в маппинг.
        """
        results = [None] * len(items)
        for batch_start in range(0, len(items), batch_size):
            chunk = items[batch_start:batch_start + batch_size]
            try:
                resp = self.post(path, chunk)
                # Проверяем ответ: может быть list объектов или list с ошибками
                if isinstance(resp, list):
                    for i, r in enumerate(resp):
                        idx = batch_start + i
                        results[idx] = r
                        if on_each_success and isinstance(r, dict) and r.get("meta"):
                            on_each_success(idx, r)
                else:
                    log.warning(f"  {path} batch вернул не-list: {type(resp)}")
            except Exception as e:
                log.warning(f"  Batch упал ({len(chunk)} объектов): {str(e)[:200]}. Fallback одиночные.")
                for i, item in enumerate(chunk):
                    idx = batch_start + i
                    try:
                        r = self.post(path, item)
                        results[idx] = r
                        if on_each_success and isinstance(r, dict) and r.get("meta"):
                            on_each_success(idx, r)
                    except Exception as e2:
                        log.warning(f"    одиночный POST {idx}: {str(e2)[:200]}")
                        results[idx] = {"error": str(e2)[:300]}
            log.info(f"  POST {path}: {min(batch_start + batch_size, len(items))}/{len(items)}")
        return results


# ── Маппинг UUID ──────────────────────────────────────────────────────────────


class UUIDMap:
    """Маппинг old_uuid → new_uuid с atomic save после каждой записи."""

    def __init__(self):
        self.data = {}
        if MAP_FILE.exists():
            try:
                self.data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
                total = sum(len(v) for v in self.data.values())
                log.info(f"Загружен маппинг: {total} записей в {len(self.data)} namespaces")
            except Exception as e:
                log.error(f"Маппинг повреждён: {e}. Старт с нуля.")
                self.data = {}

    def save(self):
        """Atomic write через temp+rename."""
        tmp = MAP_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(MAP_FILE)

    def set(self, ns: str, old_uuid: str, new_uuid: str, save: bool = True):
        self.data.setdefault(ns, {})[old_uuid] = new_uuid
        if save:
            self.save()

    def get(self, ns: str, old_uuid: str):
        return self.data.get(ns, {}).get(old_uuid)

    def has(self, ns: str, old_uuid: str) -> bool:
        return old_uuid in self.data.get(ns, {})

    def count(self, ns: str) -> int:
        return len(self.data.get(ns, {}))


# ── Утилиты ───────────────────────────────────────────────────────────────────


def uuid_from_href(href: str) -> str:
    return href.split("/")[-1] if href else ""


def meta(href: str, entity_type: str) -> dict:
    return {
        "meta": {
            "href": href,
            "type": entity_type,
            "mediaType": "application/json",
        }
    }


def entity_type_from_href(href: str) -> str:
    """https://.../entity/customerorder/{uuid} → customerorder"""
    if "/entity/" not in href:
        return ""
    return href.split("/entity/")[-1].split("/")[0]


# ── Мигратор ──────────────────────────────────────────────────────────────────


class Migrator:
    DOC_TYPES = [
        "customerorder", "demand", "invoiceout", "invoicein",
        "supply", "paymentin", "paymentout", "move", "loss",
        "cashin", "cashout",
    ]

    EXTRA_DOC_TYPES = [
        "purchaseorder", "purchasereturn", "factureout", "enter", "processingorder",
    ]

    def __init__(self, pilot: bool = False, catchup: bool = False, extras: bool = False):
        if not NEW_TOKEN:
            log.error("NEW_TOKEN не задан")
            sys.exit(1)
        self.src = MS(OLD_TOKEN, label="OLD")
        self.dst = MS(NEW_TOKEN, label="NEW")
        self.umap = UUIDMap()
        self.pilot = pilot
        self.catchup = catchup
        self.extras = extras
        self._default_currency_meta = None
        self._default_pricetype_meta = None
        self._pilot_product_uuids = set()
        self._pilot_service_uuids = set()
        self._pilot_counterparty_uuids = set()
        self._missing_assortment_uuids = {}  # uuid → entity_type (product/service)
        self._init_new_account_refs()
        mode = 'PILOT' if pilot else ('CATCHUP' if catchup else ('EXTRAS' if extras else 'FULL'))
        log.info(f"Режим: {mode}")

    def _init_new_account_refs(self):
        """Валюта и тип цены нового аккаунта (старые href в ценах ломают POST)."""
        currencies = self.dst.get_all("entity/currency")
        for c in currencies:
            if c.get("default") or c.get("name") == "руб":
                self._default_currency_meta = c["meta"]
                break
        if not self._default_currency_meta and currencies:
            self._default_currency_meta = currencies[0]["meta"]
        try:
            price_types = self.dst.get("context/companysettings/pricetype")
            if isinstance(price_types, list) and price_types:
                self._default_pricetype_meta = price_types[0].get("meta")
        except Exception as e:
            log.warning(f"Типы цен: {e}")
        log.info(
            f"Новый аккаунт: валюта={self._default_currency_meta is not None}, "
            f"тип цены={self._default_pricetype_meta is not None}"
        )

    # ── 1. Справочники customentity ───────────────────────────────────────────

    def migrate_custom_entities(self):
        """
        Переносит справочники customentity (Канал продаж, Способ оплаты и т.д.)
        и их значения. Маппинг: customentity_dict, customentity_value.
        """
        # Получаем список справочников через метаданные компании
        try:
            company_meta = self.src.get("entity/companysettings/metadata")
        except Exception as e:
            log.warning(f"Не могу получить companysettings/metadata: {e}")
            company_meta = {}
        dicts = company_meta.get("customEntities", [])
        if not dicts:
            # Fallback: попробуем достать через атрибуты документов
            dicts = self._discover_customentity_dicts()
        log.info(f"Справочников customentity: {len(dicts)}")

        for d in dicts:
            old_dict_uuid = uuid_from_href(d["meta"]["href"])
            dict_name = d.get("name") or d.get("entityMeta", {}).get("name", "?")
            if self.umap.has("customentity_dict", old_dict_uuid):
                continue
            # Создаём справочник в новом аккаунте
            body = {"name": dict_name}
            try:
                resp = self.dst.post("entity/customentity", body)
                new_dict_uuid = uuid_from_href(resp["meta"]["href"])
                self.umap.set("customentity_dict", old_dict_uuid, new_dict_uuid)
                log.info(f"  + Справочник: {dict_name}")
            except Exception as e:
                msg = str(e)
                if "уже существует" in msg or "already exists" in msg.lower():
                    # Дубль — найдём по имени
                    existing = self.dst.get_all("entity/customentity")
                    for ex in existing:
                        if ex.get("name") == dict_name:
                            self.umap.set(
                                "customentity_dict", old_dict_uuid,
                                uuid_from_href(ex["meta"]["href"])
                            )
                            log.info(f"  → существует: {dict_name}")
                            break
                else:
                    log.error(f"  ✗ Справочник {dict_name}: {msg[:300]}")
                    continue

            # Переносим значения справочника
            new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
            if not new_dict_uuid:
                continue
            try:
                old_values = self.src.get_all(f"entity/customentity/{old_dict_uuid}")
            except Exception as e:
                log.warning(f"  Значения {dict_name}: {e}")
                continue
            log.info(f"    значений: {len(old_values)}")
            for v in old_values:
                old_val_uuid = uuid_from_href(v["meta"]["href"])
                if self.umap.has("customentity_value", old_val_uuid):
                    continue
                v_body = {
                    "name": v["name"],
                    "code": v.get("code"),
                    "description": v.get("description"),
                    "externalCode": v.get("externalCode") or old_val_uuid,
                }
                v_body = {k: vv for k, vv in v_body.items() if vv is not None}
                try:
                    r = self.dst.post(f"entity/customentity/{new_dict_uuid}", v_body)
                    self.umap.set(
                        "customentity_value", old_val_uuid,
                        uuid_from_href(r["meta"]["href"])
                    )
                except Exception as e:
                    log.warning(f"      ✗ {v.get('name','?')}: {str(e)[:150]}")

    def _discover_customentity_dicts(self) -> list:
        """Fallback: ищем customentity через атрибуты документов, товаров и услуг."""
        seen = {}
        scan_types = list(self.DOC_TYPES) + ["product", "service"]
        for entity_type in scan_types:
            try:
                attrs = self.src.get(f"entity/{entity_type}/metadata/attributes").get("rows", [])
            except Exception:
                continue
            for attr in attrs:
                if attr.get("type") == "customentity":
                    ce_meta = attr.get("customEntityMeta", {})
                    href = ce_meta.get("href", "")
                    if href and href not in seen:
                        seen[href] = {
                            "name": attr.get("name", "?"),
                            "meta": {"href": href, "type": "customentitymetadata"},
                        }
        return list(seen.values())

    # ── 2. Кастомные атрибуты документов ──────────────────────────────────────

    def migrate_document_attributes(self):
        """Переносит метаданные атрибутов для всех типов документов."""
        for doc_type in self.DOC_TYPES:
            try:
                attrs = self.src.get(f"entity/{doc_type}/metadata/attributes").get("rows", [])
            except Exception:
                continue
            if not attrs:
                continue
            log.info(f"Атрибуты {doc_type}: {len(attrs)}")
            for attr in attrs:
                old_uuid = uuid_from_href(attr["meta"]["href"])
                if self.umap.has(f"attr_{doc_type}", old_uuid):
                    continue
                body = {
                    "name": attr["name"],
                    "type": attr["type"],
                    "required": attr.get("required", False),
                }
                # customentity: привязка к новому справочнику
                if attr.get("type") == "customentity":
                    ce_meta = attr.get("customEntityMeta", {})
                    old_dict_uuid = uuid_from_href(ce_meta.get("href", ""))
                    new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
                    if new_dict_uuid:
                        body["customEntityMeta"] = {
                            "href": f"{BASE}/entity/customentity/{new_dict_uuid}/metadata",
                            "type": "customentitymetadata",
                            "mediaType": "application/json",
                        }
                    else:
                        log.warning(f"  атрибут {attr['name']}: customentity справочник не найден, пропускаю")
                        continue
                try:
                    r = self.dst.post(f"entity/{doc_type}/metadata/attributes", body)
                    self.umap.set(f"attr_{doc_type}", old_uuid, uuid_from_href(r["meta"]["href"]))
                    log.info(f"  + {doc_type}/{attr['name']}")
                except Exception as e:
                    log.warning(f"  ✗ {doc_type}/{attr['name']}: {str(e)[:200]}")

    # ── 2.5. Кастомные атрибуты товаров и услуг ───────────────────────────────

    def migrate_product_service_attrs(self):
        """Переносит метаданные атрибутов для entity/product и entity/service."""
        for entity_type in ("product", "service"):
            try:
                attrs = self.src.get(f"entity/{entity_type}/metadata/attributes").get("rows", [])
            except Exception as e:
                log.warning(f"Атрибуты {entity_type}: {e}")
                continue
            if not attrs:
                log.info(f"Атрибуты {entity_type}: нет")
                continue
            log.info(f"Атрибуты {entity_type}: {len(attrs)}")
            for attr in attrs:
                old_uuid = uuid_from_href(attr["meta"]["href"])
                ns = f"attr_{entity_type}"
                if self.umap.has(ns, old_uuid):
                    continue
                body = {
                    "name": attr["name"],
                    "type": attr["type"],
                    "required": attr.get("required", False),
                }
                if attr.get("type") == "customentity":
                    ce_meta = attr.get("customEntityMeta", {})
                    old_dict_uuid = uuid_from_href(ce_meta.get("href", ""))
                    new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
                    if new_dict_uuid:
                        body["customEntityMeta"] = {
                            "href": f"{BASE}/entity/customentity/{new_dict_uuid}/metadata",
                            "type": "customentitymetadata",
                            "mediaType": "application/json",
                        }
                    else:
                        log.warning(f"  атрибут {attr['name']}: customentity справочник не найден, пропускаю")
                        continue
                try:
                    r = self.dst.post(f"entity/{entity_type}/metadata/attributes", body)
                    self.umap.set(ns, old_uuid, uuid_from_href(r["meta"]["href"]))
                    log.info(f"  + {entity_type}/{attr['name']}")
                except Exception as e:
                    log.warning(f"  ✗ {entity_type}/{attr['name']}: {str(e)[:200]}")

    # ── 3. Организации ────────────────────────────────────────────────────────

    def migrate_organizations(self):
        orgs = self.src.get_all("entity/organization")
        log.info(f"Организации: {len(orgs)}")
        # Индексируем существующие в новом аккаунте по ИНН и по имени
        dst_orgs = self.dst.get_all("entity/organization")
        dst_by_inn = {o["inn"]: uuid_from_href(o["meta"]["href"])
                      for o in dst_orgs if o.get("inn")}
        dst_by_name = {o["name"]: uuid_from_href(o["meta"]["href"])
                       for o in dst_orgs}
        for org in orgs:
            old_uuid = uuid_from_href(org["meta"]["href"])
            if self.umap.has("organization", old_uuid):
                continue
            # Сначала ищем по ИНН, потом по имени — не создаём дубль
            match_uuid = (dst_by_inn.get(org.get("inn") or "") or
                          dst_by_name.get(org["name"]))
            if match_uuid:
                self.umap.set("organization", old_uuid, match_uuid)
                log.info(f"  → существует: {org['name']}")
                continue
            body = self._copy_fields(org, [
                "name", "fullName", "legalTitle", "inn", "kpp", "ogrn", "ogrnip",
                "legalAddress", "actualAddress", "email", "phone", "companyType",
                "code", "description",
            ])
            body["externalCode"] = old_uuid
            try:
                r = self.dst.post("entity/organization", body)
                self.umap.set("organization", old_uuid, uuid_from_href(r["meta"]["href"]))
                log.info(f"  + {org['name']}")
            except Exception as e:
                log.warning(f"  ✗ {org['name']}: {str(e)[:200]}")

    # ── 4. UOM ────────────────────────────────────────────────────────────────

    def migrate_uom(self):
        all_uom = self.src.get_all("entity/uom")
        dst_uom_by_name = {u["name"]: uuid_from_href(u["meta"]["href"])
                            for u in self.dst.get_all("entity/uom")}
        # Системные UOM маппим по имени
        for u in all_uom:
            old_uuid = uuid_from_href(u["meta"]["href"])
            if self.umap.has("uom", old_uuid):
                continue
            if u["name"] in dst_uom_by_name:
                self.umap.set("uom", old_uuid, dst_uom_by_name[u["name"]])
                continue
            # Создаём новый
            body = {"name": u["name"], "code": u.get("code"),
                    "description": u.get("description")}
            body = {k: v for k, v in body.items() if v is not None}
            try:
                r = self.dst.post("entity/uom", body)
                self.umap.set("uom", old_uuid, uuid_from_href(r["meta"]["href"]))
            except Exception as e:
                log.warning(f"  ✗ UOM {u['name']}: {str(e)[:200]}")
        log.info(f"UOM смаплено: {self.umap.count('uom')}")

    # ── 5. Склады ─────────────────────────────────────────────────────────────

    def migrate_stores(self):
        stores = self.src.get_all("entity/store")
        dst_by_name = {s["name"]: uuid_from_href(s["meta"]["href"])
                       for s in self.dst.get_all("entity/store")}
        log.info(f"Склады: {len(stores)}")
        for store in stores:
            old_uuid = uuid_from_href(store["meta"]["href"])
            if self.umap.has("store", old_uuid):
                continue
            if store["name"] in dst_by_name:
                self.umap.set("store", old_uuid, dst_by_name[store["name"]])
                log.info(f"  → существует: {store['name']}")
                continue
            body = self._copy_fields(store, ["name", "address", "description", "code"])
            body["externalCode"] = old_uuid
            try:
                r = self.dst.post("entity/store", body)
                self.umap.set("store", old_uuid, uuid_from_href(r["meta"]["href"]))
                log.info(f"  + {store['name']}")
            except Exception as e:
                log.warning(f"  ✗ {store['name']}: {str(e)[:200]}")

    # ── 6. Папки номенклатуры ─────────────────────────────────────────────────

    def migrate_product_folders(self):
        folders = self.src.get_all("entity/productfolder")
        log.info(f"Папки: {len(folders)}")
        by_uuid = {uuid_from_href(f["meta"]["href"]): f for f in folders}
        seen = set()
        ordered = []

        def add(f):
            uid = uuid_from_href(f["meta"]["href"])
            if uid in seen:
                return
            parent_href = (f.get("productFolder") or {}).get("meta", {}).get("href")
            if parent_href:
                parent_uuid = uuid_from_href(parent_href)
                if parent_uuid in by_uuid and parent_uuid not in seen:
                    add(by_uuid[parent_uuid])
            seen.add(uid)
            ordered.append(f)

        for f in folders:
            add(f)

        for folder in ordered:
            old_uuid = uuid_from_href(folder["meta"]["href"])
            if self.umap.has("productfolder", old_uuid):
                continue
            body = self._copy_fields(folder, ["name", "code", "description"])
            body["externalCode"] = old_uuid
            parent_href = (folder.get("productFolder") or {}).get("meta", {}).get("href")
            if parent_href:
                new_parent = self.umap.get("productfolder", uuid_from_href(parent_href))
                if new_parent:
                    body["productFolder"] = meta(
                        f"{BASE}/entity/productfolder/{new_parent}", "productfolder"
                    )
            try:
                r = self.dst.post("entity/productfolder", body)
                self.umap.set("productfolder", old_uuid, uuid_from_href(r["meta"]["href"]))
                log.info(f"  + {folder['name']}")
            except Exception as e:
                log.warning(f"  ✗ {folder['name']}: {str(e)[:200]}")

    # ── 7. Товары и услуги ────────────────────────────────────────────────────

    def collect_pilot_dependencies(self):
        """Собирает UUID товаров/услуг/контрагентов из пилотных документов."""
        log.info("Pilot: собираю зависимости из пилотных документов...")
        for doc_type, has_pos in self.DOC_ORDER:
            params = {"filter": f"moment>={YEAR_START}"}
            docs = self.src.get_all(f"entity/{doc_type}", params=params)[:PILOT_DOCS_PER_TYPE]
            for doc in docs:
                old_uuid = uuid_from_href(doc["meta"]["href"])
                agent_href = (doc.get("agent") or {}).get("meta", {}).get("href", "")
                if agent_href and entity_type_from_href(agent_href) == "counterparty":
                    self._pilot_counterparty_uuids.add(uuid_from_href(agent_href))
                if not has_pos:
                    continue
                try:
                    full = self.src.get(
                        f"entity/{doc_type}/{old_uuid}",
                        params={"expand": "positions"},
                    )
                except Exception:
                    continue
                pos_data = full.get("positions", {})
                rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else (pos_data or [])
                for p in rows:
                    a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
                    a_uuid = uuid_from_href(a_href)
                    et = entity_type_from_href(a_href)
                    if et == "product":
                        self._pilot_product_uuids.add(a_uuid)
                    elif et == "service":
                        self._pilot_service_uuids.add(a_uuid)
        log.info(
            f"Pilot deps: товаров={len(self._pilot_product_uuids)}, "
            f"услуг={len(self._pilot_service_uuids)}, "
            f"контрагентов={len(self._pilot_counterparty_uuids)}"
        )

    def _fetch_entities_by_uuids(self, entity_type: str, uuids: set,
                                   expand: str = None) -> list:
        items = []
        for uid in uuids:
            try:
                params = {"expand": expand} if expand else None
                items.append(self.src.get(f"entity/{entity_type}/{uid}", params=params))
            except Exception as e:
                log.warning(f"  не найден {entity_type}/{uid}: {str(e)[:100]}")
        return items

    def migrate_products(self):
        if self.pilot and not self._pilot_product_uuids:
            self.collect_pilot_dependencies()
        for entity_type, limit in (("product", PILOT_PRODUCTS),
                                    ("service", PILOT_SERVICES)):
            if self.pilot:
                uuids = (self._pilot_product_uuids if entity_type == "product"
                         else self._pilot_service_uuids)
                items = self._fetch_entities_by_uuids(entity_type, uuids, expand="attributes")
            else:
                items = self.src.get_all(f"entity/{entity_type}", expand="attributes")
            log.info(f"{entity_type}: {len(items)}" + (" (pilot)" if self.pilot else ""))
            to_create = []
            old_uuids = []
            for item in items:
                old_uuid = uuid_from_href(item["meta"]["href"])
                if self.umap.has(entity_type, old_uuid):
                    continue
                body = self._map_product_body(item, entity_type)
                body["externalCode"] = old_uuid
                to_create.append(body)
                old_uuids.append(old_uuid)
            if not to_create:
                log.info(f"  {entity_type}: всё перенесено")
                continue

            def on_success(idx, r):
                self.umap.set(entity_type, old_uuids[idx], uuid_from_href(r["meta"]["href"]),
                              save=False)

            self.dst.post_batch(f"entity/{entity_type}", to_create,
                                 batch_size=500, on_each_success=on_success)
            self.umap.save()

    def _map_product_attrs(self, attrs: list, entity_type: str) -> list:
        result = []
        ns = f"attr_{entity_type}"
        for attr in attrs:
            old_attr_uuid = uuid_from_href(attr.get("meta", {}).get("href", ""))
            new_attr_uuid = self.umap.get(ns, old_attr_uuid)
            if not new_attr_uuid:
                continue
            attr_type = attr.get("type")
            value = attr.get("value")
            if attr_type == "customentity" and isinstance(value, dict):
                val_href = value.get("meta", {}).get("href", "")
                old_val_uuid = uuid_from_href(val_href)
                new_val_uuid = self.umap.get("customentity_value", old_val_uuid)
                if not new_val_uuid:
                    continue
                parts = val_href.split("/")
                if len(parts) >= 2:
                    old_dict_uuid = parts[-2]
                    new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
                    if new_dict_uuid:
                        value = {
                            "meta": {
                                "href": f"{BASE}/entity/customentity/{new_dict_uuid}/{new_val_uuid}",
                                "type": "customentity",
                                "mediaType": "application/json",
                            }
                        }
                    else:
                        continue
                else:
                    continue
            if value is None:
                continue
            result.append({
                "meta": {
                    "href": f"{BASE}/entity/{entity_type}/metadata/attributes/{new_attr_uuid}",
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "value": value,
            })
        return result

    def _map_product_body(self, item: dict, entity_type: str) -> dict:
        body = self._copy_fields(item, [
            "name", "description", "article", "weight", "volume",
            "vat", "vatEnabled",
        ])
        # code не копируем — в новом аккаунте может конфликтовать с авто-генерацией
        # minPrice/effectiveVat/country — могут содержать href старого аккаунта
        folder_href = (item.get("productFolder") or {}).get("meta", {}).get("href")
        if folder_href:
            new_folder = self.umap.get("productfolder", uuid_from_href(folder_href))
            if new_folder:
                body["productFolder"] = meta(
                    f"{BASE}/entity/productfolder/{new_folder}", "productfolder"
                )
        uom_href = (item.get("uom") or {}).get("meta", {}).get("href")
        if uom_href:
            new_uom = self.umap.get("uom", uuid_from_href(uom_href))
            if new_uom:
                body["uom"] = meta(f"{BASE}/entity/uom/{new_uom}", "uom")
        if item.get("salePrices") and self._default_currency_meta:
            body["salePrices"] = []
            for p in item["salePrices"]:
                sp = {
                    "value": p.get("value", 0),
                    "currency": {"meta": self._default_currency_meta},
                }
                if self._default_pricetype_meta:
                    sp["priceType"] = {"meta": self._default_pricetype_meta}
                body["salePrices"].append(sp)
        if item.get("buyPrice") and self._default_currency_meta:
            body["buyPrice"] = {
                "value": item["buyPrice"].get("value", 0),
                "currency": {"meta": self._default_currency_meta},
            }
        if entity_type == "product" and item.get("trackingType"):
            body["trackingType"] = item["trackingType"]
        mapped_attrs = self._map_product_attrs(item.get("attributes", []), entity_type)
        if mapped_attrs:
            body["attributes"] = mapped_attrs
        return body

    # ── 7a. Варианты (модификации товаров) ────────────────────────────────────

    def _ensure_product_mapped(self, old_uuid: str) -> bool:
        """Создаёт product в новом аккаунте, если его ещё нет в uuid_map."""
        if not old_uuid:
            return False
        if self.umap.has("product", old_uuid):
            return True
        try:
            item = self.src.get(f"entity/product/{old_uuid}", params={"expand": "attributes"})
        except Exception as e:
            log.warning(f"  ✗ product/{old_uuid}: не найден в src ({str(e)[:80]})")
            return False
        body = self._map_product_body(item, "product")
        body["externalCode"] = old_uuid
        if item.get("archived"):
            body["archived"] = True
        try:
            r = self.dst.post("entity/product", body)
            self.umap.set("product", old_uuid, uuid_from_href(r["meta"]["href"]))
            log.info(f"  + product (parent): {item.get('name')}")
            return True
        except Exception as e:
            err = str(e)
            if "3006" in err:
                existing = self.dst.get_all(
                    "entity/product",
                    params={"filter": f"externalCode={old_uuid}"},
                )
                if existing:
                    self.umap.set("product", old_uuid, uuid_from_href(existing[0]["meta"]["href"]))
                    log.info(f"  → product существует: {item.get('name')}")
                    return True
            log.warning(f"  ✗ product/{old_uuid}: {err[:150]}")
            return False

    def _create_variant(self, v: dict) -> bool:
        """Создаёт один variant, возвращает True при успехе."""
        old_uuid = uuid_from_href(v["meta"]["href"])
        if self.umap.has("variant", old_uuid):
            return True
        product_href = (v.get("product") or {}).get("meta", {}).get("href", "")
        old_product_uuid = uuid_from_href(product_href)
        if not self.umap.get("product", old_product_uuid):
            if not self._ensure_product_mapped(old_product_uuid):
                log.warning(f"  ✗ variant {v.get('name')}: родительский товар не смаплен")
                return False
        new_product_uuid = self.umap.get("product", old_product_uuid)
        body = {
            "product": meta(f"{BASE}/entity/product/{new_product_uuid}", "product"),
            "externalCode": old_uuid,
        }
        if v.get("characteristics"):
            body["characteristics"] = [
                {"name": c["name"], "value": c["value"]}
                for c in v["characteristics"]
                if c.get("name") and c.get("value") is not None
            ]
        for field in ("code", "description"):
            if v.get(field):
                body[field] = v[field]
        if v.get("salePrices") and self._default_currency_meta:
            body["salePrices"] = []
            for p in v["salePrices"]:
                sp = {"value": p.get("value", 0), "currency": {"meta": self._default_currency_meta}}
                if self._default_pricetype_meta:
                    sp["priceType"] = {"meta": self._default_pricetype_meta}
                body["salePrices"].append(sp)
        if v.get("buyPrice") and self._default_currency_meta:
            body["buyPrice"] = {
                "value": v["buyPrice"].get("value", 0),
                "currency": {"meta": self._default_currency_meta},
            }
        try:
            r = self.dst.post("entity/variant", body)
            self.umap.set("variant", old_uuid, uuid_from_href(r["meta"]["href"]), save=False)
            return True
        except Exception as e:
            err = str(e)
            if "3006" in err:
                existing = self.dst.get_all(
                    "entity/variant",
                    params={"filter": f"externalCode={old_uuid}"},
                )
                if existing:
                    self.umap.set("variant", old_uuid,
                                  uuid_from_href(existing[0]["meta"]["href"]), save=False)
                    return True
            log.warning(f"  ✗ variant {v.get('name')}: {err[:200]}")
            return False

    def migrate_variants(self):
        """
        Переносит entity/variant (товарные модификации).
        При отсутствии родителя — догружает его из старого аккаунта.
        """
        variants = self.src.get_all("entity/variant")
        log.info(f"Варианты (variant): {len(variants)}")
        created = 0
        skipped = 0
        for v in variants:
            old_uuid = uuid_from_href(v["meta"]["href"])
            if self.umap.has("variant", old_uuid):
                skipped += 1
                continue
            if self._create_variant(v):
                created += 1
        self.umap.save()
        log.info(f"  variant: создано={created}, пропущено={skipped}")

    def migrate_missing_variants(self):
        """Догружает variant из _missing_assortment_uuids (собранных при миграции документов)."""
        missing = {
            uid: et for uid, et in self._missing_assortment_uuids.items()
            if et == "variant" and not self.umap.has("variant", uid)
        }
        log.info(f"Недостающих variant: {len(missing)}")
        if not missing:
            return
        created = 0
        for uid in missing:
            try:
                v = self.src.get(f"entity/variant/{uid}")
            except Exception as e:
                log.warning(f"  ✗ get variant/{uid}: {str(e)[:100]}")
                continue
            if self._create_variant(v):
                created += 1
        self.umap.save()
        log.info(f"  missing variant: создано={created}/{len(missing)}")

    # ── 7b. Обновление атрибутов уже созданных товаров/услуг ──────────────────

    def patch_product_attrs(self):
        """
        PUT атрибуты для товаров/услуг, созданных ранее без атрибутов.
        Пропускает если атрибутов нет в старом аккаунте (нечего мигрировать).
        """
        for entity_type in ("product", "service"):
            ns = f"attr_{entity_type}"
            if not self.umap.count(ns):
                log.info(f"patch_attrs {entity_type}: нет атрибутов, пропускаю")
                continue
            mapped = self.umap.data.get(entity_type, {})
            if not mapped:
                continue
            log.info(f"patch_attrs {entity_type}: обновляю {len(mapped)} записей...")
            updated = 0
            skipped = 0
            for old_uuid, new_uuid in mapped.items():
                try:
                    old_item = self.src.get(
                        f"entity/{entity_type}/{old_uuid}",
                        params={"expand": "attributes"},
                    )
                except Exception as e:
                    log.warning(f"  ✗ get {entity_type}/{old_uuid}: {str(e)[:100]}")
                    continue
                raw_attrs = old_item.get("attributes", [])
                if not raw_attrs:
                    skipped += 1
                    continue
                mapped_attrs = self._map_product_attrs(raw_attrs, entity_type)
                if not mapped_attrs:
                    skipped += 1
                    continue
                try:
                    self.dst.put(f"entity/{entity_type}/{new_uuid}", {"attributes": mapped_attrs})
                    updated += 1
                except Exception as e:
                    log.warning(f"  ✗ PUT {entity_type}/{new_uuid}: {str(e)[:200]}")
            log.info(f"  patch_attrs {entity_type}: обновлено={updated}, без атрибутов={skipped}")

    # ── 8. Контрагенты ────────────────────────────────────────────────────────

    def migrate_counterparties(self):
        if self.pilot:
            if not self._pilot_counterparty_uuids:
                self.collect_pilot_dependencies()
            cps = self._fetch_entities_by_uuids("counterparty", self._pilot_counterparty_uuids)
        else:
            cps = self.src.get_all("entity/counterparty")
        log.info(f"Контрагенты: {len(cps)}" + (" (pilot)" if self.pilot else ""))
        to_create = []
        old_uuids = []
        for cp in cps:
            old_uuid = uuid_from_href(cp["meta"]["href"])
            if self.umap.has("counterparty", old_uuid):
                continue
            body = self._copy_fields(cp, [
                "name", "companyType", "inn", "kpp", "ogrn", "ogrnip",
                "legalTitle", "legalAddress", "actualAddress", "email", "phone",
                "description", "code",
            ])
            body["externalCode"] = old_uuid
            to_create.append(body)
            old_uuids.append(old_uuid)

        def on_success(idx, r):
            self.umap.set("counterparty", old_uuids[idx], uuid_from_href(r["meta"]["href"]),
                          save=False)

        self.dst.post_batch("entity/counterparty", to_create,
                             batch_size=500, on_each_success=on_success)
        self.umap.save()
        log.info(f"Контрагенты смаплено: {self.umap.count('counterparty')}")

    # ── 9. Ввод остатков ──────────────────────────────────────────────────────

    def migrate_stock_balance(self):
        if self.pilot:
            log.info("Pilot: пропускаю ввод остатков (нужен для full run)")
            return

        log.info("Запрашиваю отчёт остатков на 01.01.2026 (async)...")
        try:
            stock = self.src.async_report(
                "report/stock/all",
                params={"momentFrom": STOCK_MOMENT, "stockMode": "all"},
            )
        except Exception as e:
            log.warning(f"Async упал: {e}. Пробую синхронно.")
            stock = self.src.get_all(
                "report/stock/all",
                params={"momentFrom": STOCK_MOMENT, "stockMode": "all"},
            )
        stock = [s for s in stock if s.get("stock", 0) != 0]
        log.info(f"Позиций с остатком: {len(stock)}")
        if not stock:
            return

        dst_orgs = self.dst.get_all("entity/organization")
        dst_stores = self.dst.get_all("entity/store")
        if not dst_orgs or not dst_stores:
            log.error("Нет организации/склада в новом аккаунте")
            return

        # Группируем остатки по складам — нужен enter на каждый склад
        by_store = {}
        for s in stock:
            store_href = (s.get("store") or {}).get("meta", {}).get("href", "")
            old_store_uuid = uuid_from_href(store_href)
            new_store_uuid = self.umap.get("store", old_store_uuid)
            if not new_store_uuid:
                continue
            by_store.setdefault(new_store_uuid, []).append(s)

        new_org_href = dst_orgs[0]["meta"]["href"]
        for new_store_uuid, items in by_store.items():
            positions = []
            for s in items:
                product_href = (s.get("meta") or {}).get("href", "")
                old_product_uuid = uuid_from_href(product_href)
                if self.umap.get("product", old_product_uuid):
                    new_uuid = self.umap.get("product", old_product_uuid)
                    new_type = "product"
                elif self.umap.get("service", old_product_uuid):
                    new_uuid = self.umap.get("service", old_product_uuid)
                    new_type = "service"
                else:
                    continue
                positions.append({
                    "assortment": meta(f"{BASE}/entity/{new_type}/{new_uuid}", new_type),
                    "quantity": s.get("stock", 0),
                    "price": int(s.get("price", 0) or 0),
                })
            if not positions:
                continue
            body = {
                "moment": STOCK_MOMENT,
                "applicable": True,
                "organization": meta(new_org_href, "organization"),
                "store": meta(f"{BASE}/entity/store/{new_store_uuid}", "store"),
                "positions": positions,
                "description": f"Ввод остатков (миграция, склад {new_store_uuid[:8]})",
                "externalCode": f"migration_enter_{new_store_uuid}",
            }
            try:
                r = self.dst.post("entity/enter", body)
                log.info(f"  + Ввод остатков ({len(positions)} позиций) на склад {new_store_uuid[:8]}")
            except Exception as e:
                log.error(f"  ✗ Ввод остатков на склад {new_store_uuid[:8]}: {str(e)[:300]}")

    # ── 10. Документы ─────────────────────────────────────────────────────────

    DOC_ORDER = [
        ("supply", True),
        ("customerorder", True),
        ("demand", True),
        ("invoiceout", True),
        ("paymentin", False),
        ("paymentout", False),
        ("cashin", False),
        ("cashout", False),
        ("move", True),
        ("loss", True),
    ]

    def migrate_documents(self):
        for doc_type, has_positions in self.DOC_ORDER:
            self._migrate_doc_type(doc_type, has_positions)

    def _migrate_doc_type(self, doc_type: str, has_positions: bool):
        # Грузим документы за 2026
        params = {"filter": f"moment>={YEAR_START}"}
        docs = self.src.get_all(f"entity/{doc_type}", params=params)
        if self.pilot:
            docs = docs[:PILOT_DOCS_PER_TYPE]
        log.info(f"{doc_type}: {len(docs)}" + (" (pilot)" if self.pilot else ""))

        # Кэшируем атрибуты этого типа (для маппинга)
        try:
            old_attrs = self.src.get(f"entity/{doc_type}/metadata/attributes").get("rows", [])
        except Exception:
            old_attrs = []
        attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

        created = 0
        skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has(doc_type, old_uuid):
                skipped += 1
                continue

            # Подгружаем полный документ с expand для позиций, атрибутов, проекта, договора
            try:
                expand = "positions,attributes,project,contract" if has_positions else "attributes,project,contract"
                full = self.src.get(
                    f"entity/{doc_type}/{old_uuid}",
                    params={"expand": expand},
                )
            except Exception as e:
                log.warning(f"  ✗ get {doc_type}/{old_uuid}: {str(e)[:150]}")
                continue

            body = self._build_doc_body(full, doc_type, has_positions, attrs_index)
            if body is None:
                continue
            try:
                r = self.dst.post(f"entity/{doc_type}", body)
                self.umap.set(doc_type, old_uuid, uuid_from_href(r["meta"]["href"]), save=False)
                created += 1
                if created % 50 == 0:
                    self.umap.save()
                    log.info(f"  {doc_type}: {created}/{len(docs)}")
            except Exception as e:
                err_str = str(e)
                # name uniqueness — документ уже создан в предыдущем запуске
                if "name" in err_str and "3006" in err_str and doc.get("name"):
                    existing_uuid = self._find_existing_doc(doc_type, doc["name"])
                    if existing_uuid:
                        self.umap.set(doc_type, old_uuid, existing_uuid, save=False)
                        log.info(f"  → найден в dst: {doc_type}/{doc.get('name')}")
                        created += 1
                        continue
                log.warning(f"  ✗ {doc_type}/{doc.get('name','?')}: {err_str[:250]}")
        self.umap.save()
        log.info(f"  {doc_type}: создано {created}, пропущено {skipped}")

    def _build_doc_body(self, doc, doc_type, has_positions, attrs_index):
        old_uuid = uuid_from_href(doc["meta"]["href"])
        body = {
            "name": doc.get("name"),
            "moment": doc.get("moment"),
            "applicable": doc.get("applicable", True),
            "description": doc.get("description"),
            "externalCode": old_uuid,
        }
        # Организация
        org_href = (doc.get("organization") or {}).get("meta", {}).get("href", "")
        new_org = self.umap.get("organization", uuid_from_href(org_href))
        if new_org:
            body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")
        # Склад
        store_href = (doc.get("store") or {}).get("meta", {}).get("href", "")
        new_store = self.umap.get("store", uuid_from_href(store_href))
        if new_store:
            body["store"] = meta(f"{BASE}/entity/store/{new_store}", "store")
        # Контрагент
        agent_href = (doc.get("agent") or {}).get("meta", {}).get("href", "")
        if agent_href:
            agent_type = entity_type_from_href(agent_href)
            if agent_type == "counterparty":
                new_agent = self.umap.get("counterparty", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/counterparty/{new_agent}", "counterparty")
            elif agent_type == "organization":
                new_agent = self.umap.get("organization", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/organization/{new_agent}", "organization")
        # Суммы / VAT. rate не переносим — содержит currency старого аккаунта
        for f in ("sum", "vatSum", "payedSum"):
            if doc.get(f) is not None:
                body[f] = doc[f]
        # Позиции
        if has_positions:
            pos_data = doc.get("positions", {})
            if isinstance(pos_data, dict):
                positions_rows = pos_data.get("rows", [])
            else:
                positions_rows = pos_data or []
            body["positions"] = self._map_positions(positions_rows)
            if not body["positions"] and positions_rows:
                # Если все позиции не смаппились — не создаём документ
                log.warning(f"  ✗ {doc_type}/{doc.get('name','?')}: позиции не смаплены")
                return None
        # Проект
        proj_href = (doc.get("project") or {}).get("meta", {}).get("href", "")
        if proj_href:
            new_proj = self.umap.get("project", uuid_from_href(proj_href))
            if new_proj:
                body["project"] = meta(f"{BASE}/entity/project/{new_proj}", "project")
        # Договор
        con_href = (doc.get("contract") or {}).get("meta", {}).get("href", "")
        if con_href:
            new_con = self.umap.get("contract", uuid_from_href(con_href))
            if new_con:
                body["contract"] = meta(f"{BASE}/entity/contract/{new_con}", "contract")
        # Атрибуты
        attrs = self._map_doc_attributes(doc.get("attributes", []), doc_type, attrs_index)
        if attrs:
            body["attributes"] = attrs
        # Специфичные
        if doc_type == "customerorder":
            if doc.get("deliveryPlannedMoment"):
                body["deliveryPlannedMoment"] = doc["deliveryPlannedMoment"]
            if doc.get("shipmentAddress"):
                body["shipmentAddress"] = doc["shipmentAddress"]
        if doc_type == "move":
            for store_key in ("sourceStore", "targetStore"):
                s_href = (doc.get(store_key) or {}).get("meta", {}).get("href", "")
                new_s = self.umap.get("store", uuid_from_href(s_href))
                if new_s:
                    body[store_key] = meta(f"{BASE}/entity/store/{new_s}", "store")
        return {k: v for k, v in body.items() if v is not None}

    def _map_positions(self, positions: list) -> list:
        result = []
        for p in positions:
            assortment = p.get("assortment") or {}
            a_href = assortment.get("meta", {}).get("href", "")
            old_uuid = uuid_from_href(a_href)
            et = entity_type_from_href(a_href)
            new_uuid = self.umap.get("product", old_uuid)
            new_type = "product"
            if not new_uuid:
                new_uuid = self.umap.get("service", old_uuid)
                new_type = "service"
            if not new_uuid:
                new_uuid = self.umap.get("variant", old_uuid)
                new_type = "variant"
            if not new_uuid:
                # Запоминаем отсутствующий UUID для последующей миграции архивных
                if et in ("product", "service") and old_uuid:
                    self._missing_assortment_uuids[old_uuid] = et
                elif et == "variant" and old_uuid:
                    self._missing_assortment_uuids[old_uuid] = "variant"
                elif old_uuid:
                    self._missing_assortment_uuids[old_uuid] = "product"
                continue
            new_pos = {
                "assortment": meta(f"{BASE}/entity/{new_type}/{new_uuid}", new_type),
                "quantity": p.get("quantity", 1),
                "price": p.get("price", 0),
                "discount": p.get("discount", 0),
                "vat": p.get("vat", 0),
            }
            if p.get("reserve") is not None:
                new_pos["reserve"] = p["reserve"]
            if p.get("shipped") is not None:
                new_pos["shipped"] = p["shipped"]
            uom_href = (p.get("uom") or {}).get("meta", {}).get("href", "")
            new_uom = self.umap.get("uom", uuid_from_href(uom_href))
            if new_uom:
                new_pos["uom"] = meta(f"{BASE}/entity/uom/{new_uom}", "uom")
            result.append(new_pos)
        return result

    def _map_doc_attributes(self, attrs: list, doc_type: str, attrs_index: dict) -> list:
        result = []
        for attr in attrs:
            old_attr_uuid = uuid_from_href(attr.get("meta", {}).get("href", ""))
            new_attr_uuid = self.umap.get(f"attr_{doc_type}", old_attr_uuid)
            if not new_attr_uuid:
                continue
            attr_type = attr.get("type") or (attrs_index.get(old_attr_uuid, {}).get("type"))
            value = attr.get("value")
            # employee — маппим через uuid_map["employee"]
            if attr_type == "employee" and isinstance(value, dict):
                emp_href = value.get("meta", {}).get("href", "")
                old_emp_uuid = uuid_from_href(emp_href)
                new_emp_uuid = self.umap.get("employee", old_emp_uuid)
                if not new_emp_uuid:
                    continue
                value = {"meta": {
                    "href": f"{BASE}/entity/employee/{new_emp_uuid}",
                    "type": "employee", "mediaType": "application/json"}}
            elif attr_type == "employee":
                continue
            # customentity — маппим значение
            if attr_type == "customentity" and isinstance(value, dict):
                val_href = value.get("meta", {}).get("href", "")
                old_val_uuid = uuid_from_href(val_href)
                new_val_uuid = self.umap.get("customentity_value", old_val_uuid)
                if not new_val_uuid:
                    continue
                # Нужен новый dict_uuid тоже
                # href формата .../customentity/{dict_uuid}/{value_uuid}
                parts = val_href.split("/")
                if len(parts) >= 2:
                    old_dict_uuid = parts[-2]
                    new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
                    if new_dict_uuid:
                        value = {
                            "meta": {
                                "href": f"{BASE}/entity/customentity/{new_dict_uuid}/{new_val_uuid}",
                                "type": "customentity",
                                "mediaType": "application/json",
                            }
                        }
                    else:
                        continue
                else:
                    continue
            new_attr = {
                "meta": {
                    "href": f"{BASE}/entity/{doc_type}/metadata/attributes/{new_attr_uuid}",
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "value": value,
            }
            result.append(new_attr)
        return result

    # ── 11. Связи платежей ─────────────────────────────────────────────────────

    def link_payments(self):
        """
        Для каждого перенесённого paymentin: тянем operations из старого,
        мапим связанные документы (customerorder/demand/invoiceout) через umap,
        делаем PUT нового платежа с этими ссылками.
        """
        pi_map = self.umap.data.get("paymentin", {})
        if not pi_map:
            log.info("Нет платежей для связки")
            return
        log.info(f"Связываю платежи с операциями: {len(pi_map)} шт")
        linked = 0
        for old_uuid, new_uuid in pi_map.items():
            try:
                old_doc = self.src.get(
                    f"entity/paymentin/{old_uuid}",
                    params={"expand": "operations"},
                )
            except Exception as e:
                log.warning(f"  ✗ get paymentin/{old_uuid}: {str(e)[:150]}")
                continue
            old_ops = old_doc.get("operations", [])
            if isinstance(old_ops, dict):
                old_ops = old_ops.get("rows", [])
            if not old_ops:
                log.info(f"  paymentin/{old_doc.get('name','?')}: нет operations")
                continue
            new_ops = []
            for op in old_ops:
                op_href = op.get("meta", {}).get("href", "")
                op_type = entity_type_from_href(op_href)
                op_old_uuid = uuid_from_href(op_href)
                op_new_uuid = self.umap.get(op_type, op_old_uuid)
                if not op_new_uuid:
                    continue
                new_op = {
                    "meta": {
                        "href": f"{BASE}/entity/{op_type}/{op_new_uuid}",
                        "type": op_type,
                        "mediaType": "application/json",
                    }
                }
                if op.get("linkedSum") is not None:
                    new_op["linkedSum"] = op["linkedSum"]
                new_ops.append(new_op)
            if not new_ops:
                continue
            try:
                self.dst.put(f"entity/paymentin/{new_uuid}", {"operations": new_ops})
                linked += 1
            except Exception as e:
                log.warning(f"  ✗ link paymentin/{new_uuid}: {str(e)[:150]}")
        log.info(f"Связано платежей: {linked}/{len(pi_map)}")

    # ── 12. Верификация ───────────────────────────────────────────────────────

    def verify(self):
        log.info("\n" + "=" * 60)
        log.info("ВЕРИФИКАЦИЯ")
        log.info("=" * 60)
        rows = []
        for path, label in [
            ("entity/organization", "Организации"),
            ("entity/counterparty", "Контрагенты"),
            ("entity/product", "Товары"),
            ("entity/service", "Услуги"),
            ("entity/store", "Склады"),
            ("entity/productfolder", "Папки"),
        ]:
            src = self.src.get(path, {"limit": 1}).get("meta", {}).get("size", "?")
            dst = self.dst.get(path, {"limit": 1}).get("meta", {}).get("size", "?")
            mark = "OK" if src == dst else "DIFF"
            rows.append(f"  [{mark}] {label}: src={src} → dst={dst}")
        for doc_type in ("supply", "customerorder", "demand", "invoiceout",
                         "paymentin", "move", "loss"):
            params = {"limit": 1, "filter": f"moment>={YEAR_START}"}
            src = self.src.get(f"entity/{doc_type}", params).get("meta", {}).get("size", "?")
            dst = self.dst.get(f"entity/{doc_type}", params).get("meta", {}).get("size", "?")
            mark = "OK" if src == dst else "DIFF"
            rows.append(f"  [{mark}] {doc_type} 2026: src={src} → dst={dst}")
        for r in rows:
            log.info(r)

    def pilot_report_urls(self):
        """URL пилотных заказов в новом аккаунте для ручной сверки."""
        log.info("\n" + "=" * 60)
        log.info("PILOT — ссылки для сверки в UI")
        log.info("=" * 60)
        orders = self.umap.data.get("customerorder", {})
        if not orders:
            log.info("  Нет перенесённых заказов — сверьте лог шага 10")
            return
        for old_uuid, new_uuid in list(orders.items())[:5]:
            try:
                old = self.src.get(f"entity/customerorder/{old_uuid}")
                log.info(
                    f"  Заказ {old.get('name')}:\n"
                    f"    Старый: https://online.moysklad.ru/app/#customerorder/edit?id={old_uuid}\n"
                    f"    Новый:  https://online.moysklad.ru/app/#customerorder/edit?id={new_uuid}"
                )
            except Exception as e:
                log.warning(f"  {old_uuid}: {e}")

    def _find_existing_doc(self, doc_type: str, name: str):
        """Ищет документ по имени в новом аккаунте, возвращает UUID или None."""
        try:
            rows = self.dst.get_all(
                f"entity/{doc_type}",
                params={"filter": f"name={name}"},
            )
            if rows:
                return uuid_from_href(rows[0]["meta"]["href"])
        except Exception as e:
            log.warning(f"  _find_existing_doc {doc_type}/{name}: {str(e)[:100]}")
        return None

    # ── 7c. Архивированные продукты из документов ─────────────────────────────

    def migrate_archived_products(self):
        """
        Переносит архивированные продукты, на которые ссылаются документы,
        но которые не попали в uuid_map (были пропущены т.к. get_all их не возвращает).
        Использует self._missing_assortment_uuids, собранный в _map_positions.
        """
        missing_uuids = {
            uid: et for uid, et in self._missing_assortment_uuids.items()
            if not self.umap.has(et, uid)
        }
        log.info(f"Недостающих UUID ассортимента: {len(missing_uuids)}")
        if not missing_uuids:
            return
        created = 0
        for uid, et in missing_uuids.items():
            if et == "variant":
                try:
                    v = self.src.get(f"entity/variant/{uid}")
                except Exception:
                    continue
                if self._create_variant(v):
                    created += 1
                continue
            # product / service
            try_types = [et] if et in ("product", "service") else ["product", "service"]
            for try_et in try_types:
                try:
                    item = self.src.get(f"entity/{try_et}/{uid}")
                except Exception:
                    continue
                if not item.get("meta"):
                    continue
                body = self._map_product_body(item, try_et)
                body["externalCode"] = uid
                if item.get("archived"):
                    body["archived"] = True
                try:
                    r = self.dst.post(f"entity/{try_et}", body)
                    self.umap.set(try_et, uid, uuid_from_href(r["meta"]["href"]))
                    log.info(f"  + {try_et} (arch): {item.get('name')}")
                    created += 1
                except Exception as e:
                    err = str(e)
                    if "3006" in err:
                        try:
                            existing = self.dst.get_all(
                                f"entity/{try_et}",
                                params={"filter": f"externalCode={uid}"},
                            )
                            if existing:
                                self.umap.set(try_et, uid, uuid_from_href(existing[0]["meta"]["href"]))
                                log.info(f"  → существует: {try_et}/{item.get('name')}")
                                created += 1
                        except Exception:
                            pass
                    else:
                        log.warning(f"  ✗ archived {try_et}/{uid}: {err[:150]}")
                break
        self.umap.save()
        log.info(f"Архивированных продуктов перенесено: {created}/{len(missing_uuids)}")

    # ── Утилиты ───────────────────────────────────────────────────────────────

    @staticmethod
    def _copy_fields(src: dict, fields: list) -> dict:
        out = {}
        for f in fields:
            if src.get(f) is not None:
                out[f] = src[f]
        return out

    def _map_assortment(self, old_uuid: str):
        """Возвращает (new_uuid, entity_type) или (None, None)."""
        for ns in ("product", "service", "variant"):
            new = self.umap.get(ns, old_uuid)
            if new:
                return new, ns
        return None, None

    # ── EXTRAS: Атрибуты новых типов ──────────────────────────────────────────

    def migrate_extra_doc_attributes(self):
        """Переносит метаданные атрибутов для EXTRA_DOC_TYPES."""
        for doc_type in self.EXTRA_DOC_TYPES:
            try:
                attrs = self.src.get(
                    f"entity/{doc_type}/metadata/attributes"
                ).get("rows", [])
            except Exception:
                continue
            if not attrs:
                log.info(f"Атрибуты {doc_type}: нет")
                continue
            log.info(f"Атрибуты {doc_type}: {len(attrs)}")
            for attr in attrs:
                old_uuid = uuid_from_href(attr["meta"]["href"])
                ns = f"attr_{doc_type}"
                if self.umap.has(ns, old_uuid):
                    continue
                body = {
                    "name": attr["name"],
                    "type": attr["type"],
                    "required": attr.get("required", False),
                }
                if attr.get("type") == "customentity":
                    ce_meta = attr.get("customEntityMeta", {})
                    old_dict_uuid = uuid_from_href(ce_meta.get("href", ""))
                    new_dict_uuid = self.umap.get("customentity_dict", old_dict_uuid)
                    if new_dict_uuid:
                        body["customEntityMeta"] = {
                            "href": f"{BASE}/entity/customentity/{new_dict_uuid}/metadata",
                            "type": "customentitymetadata",
                            "mediaType": "application/json",
                        }
                    else:
                        log.warning(f"  {doc_type}/{attr['name']}: customentity не смаплен, пропуск")
                        continue
                try:
                    r = self.dst.post(f"entity/{doc_type}/metadata/attributes", body)
                    self.umap.set(ns, old_uuid, uuid_from_href(r["meta"]["href"]))
                    log.info(f"  + {doc_type}/{attr['name']}")
                except Exception as e:
                    log.warning(f"  ✗ {doc_type}/{attr['name']}: {str(e)[:200]}")

    # ── EXTRAS: Статусы ───────────────────────────────────────────────────────

    def _ensure_state(self, entity_type: str, old_state_data: dict) -> Optional[dict]:
        """
        Берёт dict state из старого документа, находит/создаёт аналог в новом.
        Возвращает meta-dict для нового state или None.
        Кэш: ns = state_{entity_type}, key = old_state_uuid, value = new_state_uuid.
        """
        if not old_state_data:
            return None
        old_href = old_state_data.get("meta", {}).get("href", "")
        old_uuid = uuid_from_href(old_href)
        state_name = old_state_data.get("name")
        if not state_name:
            # Подгрузим имя
            try:
                s = self.src._req("GET", old_href)
                if s.ok:
                    state_name = s.json().get("name")
            except Exception:
                pass
        if not state_name:
            return None

        ns = f"state_{entity_type}"
        cached = self.umap.get(ns, old_uuid)
        if cached:
            return meta(f"{BASE}/entity/{entity_type}/metadata/states/{cached}", "state")

        # Ищем в новом аккаунте по имени
        try:
            dst_states = self.dst.get(
                f"entity/{entity_type}/metadata/states"
            ).get("rows", [])
        except Exception:
            dst_states = []

        for s in dst_states:
            if s.get("name") == state_name:
                new_uuid = uuid_from_href(s["meta"]["href"])
                self.umap.set(ns, old_uuid, new_uuid)
                return meta(f"{BASE}/entity/{entity_type}/metadata/states/{new_uuid}", "state")

        # Создаём новый статус (color обязателен)
        try:
            r = self.dst.post(
                f"entity/{entity_type}/metadata/states",
                {
                    "name": state_name,
                    "stateType": old_state_data.get("stateType", "Regular"),
                    "color": old_state_data.get("color", 15106326),  # серый по умолчанию
                },
            )
            new_uuid = uuid_from_href(r["meta"]["href"])
            self.umap.set(ns, old_uuid, new_uuid)
            log.info(f"  + state {entity_type}/{state_name}")
            return meta(f"{BASE}/entity/{entity_type}/metadata/states/{new_uuid}", "state")
        except Exception as e:
            log.warning(f"  ✗ state {entity_type}/{state_name}: {str(e)[:150]}")
            return None

    # ── EXTRAS: Папки производства ────────────────────────────────────────────

    def migrate_processing_plan_folders(self):
        folders = self.src.get_all("entity/processingplanfolder")
        log.info(f"Папки техкарт: {len(folders)}")
        by_uuid = {uuid_from_href(f["meta"]["href"]): f for f in folders}
        seen = set()
        ordered = []

        def add(f):
            uid = uuid_from_href(f["meta"]["href"])
            if uid in seen:
                return
            parent = (f.get("parent") or {}).get("meta", {}).get("href")
            if parent:
                p_uuid = uuid_from_href(parent)
                if p_uuid in by_uuid and p_uuid not in seen:
                    add(by_uuid[p_uuid])
            seen.add(uid)
            ordered.append(f)

        for f in folders:
            add(f)

        for folder in ordered:
            old_uuid = uuid_from_href(folder["meta"]["href"])
            if self.umap.has("processingplanfolder", old_uuid):
                continue
            body = self._copy_fields(folder, ["name", "code", "description"])
            body["externalCode"] = old_uuid
            parent_href = (folder.get("parent") or {}).get("meta", {}).get("href")
            if parent_href:
                new_p = self.umap.get("processingplanfolder", uuid_from_href(parent_href))
                if new_p:
                    body["parent"] = meta(
                        f"{BASE}/entity/processingplanfolder/{new_p}", "processingplanfolder"
                    )
            try:
                r = self.dst.post("entity/processingplanfolder", body)
                self.umap.set(
                    "processingplanfolder", old_uuid,
                    uuid_from_href(r["meta"]["href"])
                )
                log.info(f"  + папка: {folder['name']}")
            except Exception as e:
                log.warning(f"  ✗ папка {folder['name']}: {str(e)[:200]}")

    # ── EXTRAS: Техкарты (processingplan) ─────────────────────────────────────

    def _resolve_assortment(self, old_uuid: str) -> tuple:
        """
        Пытается найти или создать ассортимент по old_uuid.
        Проверяет variant → product → service в старом аккаунте.
        Возвращает (new_uuid, entity_type) или (None, None).
        """
        new_uuid, new_type = self._map_assortment(old_uuid)
        if new_uuid:
            return new_uuid, new_type
        # Пробуем определить тип и создать
        for try_type in ("variant", "product", "service"):
            try:
                item = self.src.get(f"entity/{try_type}/{old_uuid}")
                if not item.get("meta"):
                    continue
            except Exception:
                continue
            if try_type == "variant":
                if self._create_variant(item):
                    new_uuid = self.umap.get("variant", old_uuid)
                    if new_uuid:
                        return new_uuid, "variant"
            else:
                body = self._map_product_body(item, try_type)
                body["externalCode"] = old_uuid
                if item.get("archived"):
                    body["archived"] = True
                try:
                    r = self.dst.post(f"entity/{try_type}", body)
                    new_uuid = uuid_from_href(r["meta"]["href"])
                    self.umap.set(try_type, old_uuid, new_uuid)
                    log.info(f"  + {try_type} (on-demand): {item.get('name')}")
                    return new_uuid, try_type
                except Exception as e:
                    if "3006" in str(e):
                        ex = self.dst.get_all(
                            f"entity/{try_type}",
                            params={"filter": f"externalCode={old_uuid}"},
                        )
                        if ex:
                            new_uuid = uuid_from_href(ex[0]["meta"]["href"])
                            self.umap.set(try_type, old_uuid, new_uuid)
                            return new_uuid, try_type
            break
        return None, None

    def _map_pp_positions(self, rows: list) -> list:
        """Маппинг позиций техкарты (materials или products) с fallback на создание."""
        result = []
        for pos in rows:
            assortment = pos.get("assortment") or {}
            a_href = assortment.get("meta", {}).get("href", "")
            old_uuid = uuid_from_href(a_href)
            new_uuid, new_type = self._resolve_assortment(old_uuid)
            if not new_uuid:
                log.warning(f"    ✗ ассортимент техкарты {old_uuid} не смаплен (пропуск)")
                continue
            result.append({
                "assortment": meta(f"{BASE}/entity/{new_type}/{new_uuid}", new_type),
                "quantity": pos.get("quantity", 1),
            })
        return result

    def migrate_processing_plans(self):
        plans = self.src.get_all("entity/processingplan")
        log.info(f"Техкарты (processingplan): {len(plans)}")
        created = skipped = 0
        for plan in plans:
            old_uuid = uuid_from_href(plan["meta"]["href"])
            if self.umap.has("processingplan", old_uuid):
                skipped += 1
                continue
            # Грузим полностью с expand
            try:
                full = self.src.get(
                    f"entity/processingplan/{old_uuid}",
                    params={"expand": "materials.assortment,products.assortment"},
                )
            except Exception as e:
                log.warning(f"  ✗ get processingplan/{old_uuid}: {str(e)[:150]}")
                continue

            body = self._copy_fields(full, ["name", "cost", "costDistributionType", "archived"])
            body["externalCode"] = old_uuid

            # Папка
            parent_href = (full.get("parent") or {}).get("meta", {}).get("href")
            if parent_href:
                new_folder = self.umap.get("processingplanfolder", uuid_from_href(parent_href))
                if new_folder:
                    body["parent"] = meta(
                        f"{BASE}/entity/processingplanfolder/{new_folder}",
                        "processingplanfolder",
                    )

            # processingProcess (опционально)
            pp_href = (full.get("processingProcess") or {}).get("meta", {}).get("href")
            if pp_href:
                # Берём первый processingprocess из нового аккаунта
                try:
                    dst_pp = self.dst.get_all("entity/processingprocess")
                    if dst_pp:
                        body["processingProcess"] = {"meta": dst_pp[0]["meta"]}
                except Exception:
                    pass

            # Материалы и продукты
            mats = (full.get("materials") or {})
            mats_rows = mats.get("rows", []) if isinstance(mats, dict) else []
            prods = (full.get("products") or {})
            prods_rows = prods.get("rows", []) if isinstance(prods, dict) else []

            mapped_mats = self._map_pp_positions(mats_rows)
            mapped_prods = self._map_pp_positions(prods_rows)

            if mapped_mats:
                body["materials"] = mapped_mats
            if mapped_prods:
                body["products"] = mapped_prods

            try:
                r = self.dst.post("entity/processingplan", body)
                self.umap.set("processingplan", old_uuid, uuid_from_href(r["meta"]["href"]))
                created += 1
                if created % 20 == 0:
                    self.umap.save()
                    log.info(f"  processingplan: {created}/{len(plans)}")
            except Exception as e:
                err = str(e)
                if "3006" in err:
                    existing = self.dst.get_all(
                        "entity/processingplan",
                        params={"filter": f"externalCode={old_uuid}"},
                    )
                    if existing:
                        self.umap.set("processingplan", old_uuid,
                                      uuid_from_href(existing[0]["meta"]["href"]))
                        skipped += 1
                        continue
                log.warning(f"  ✗ processingplan/{full.get('name','?')}: {err[:250]}")
        self.umap.save()
        log.info(f"  processingplan: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Заказы на производство ────────────────────────────────────────

    def migrate_processing_orders(self):
        docs = self.src.get_all(
            "entity/processingorder",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Заказы на производство: {len(docs)}")
        try:
            old_attrs = self.src.get(
                "entity/processingorder/metadata/attributes"
            ).get("rows", [])
        except Exception:
            old_attrs = []
        attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("processingorder", old_uuid):
                skipped += 1
                continue
            try:
                full = self.src.get(
                    f"entity/processingorder/{old_uuid}",
                    params={"expand": "positions,attributes,state"},
                )
            except Exception as e:
                log.warning(f"  ✗ get processingorder/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "quantity": full.get("quantity", 1),
                "externalCode": old_uuid,
            }
            if full.get("deliveryPlannedMoment"):
                body["deliveryPlannedMoment"] = full["deliveryPlannedMoment"]

            # Организация
            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            # Склад
            store_href = (full.get("store") or {}).get("meta", {}).get("href", "")
            new_store = self.umap.get("store", uuid_from_href(store_href))
            if new_store:
                body["store"] = meta(f"{BASE}/entity/store/{new_store}", "store")

            # Техкарта
            plan_href = (full.get("processingPlan") or {}).get("meta", {}).get("href", "")
            new_plan = self.umap.get("processingplan", uuid_from_href(plan_href))
            if new_plan:
                body["processingPlan"] = meta(
                    f"{BASE}/entity/processingplan/{new_plan}", "processingplan"
                )

            # Статус
            state_data = full.get("state")
            if state_data:
                new_state_meta = self._ensure_state("processingorder", state_data)
                if new_state_meta:
                    body["state"] = new_state_meta

            # Позиции (output products of the plan)
            pos_data = full.get("positions", {})
            pos_rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else []
            positions = []
            for p in pos_rows:
                a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
                old_a = uuid_from_href(a_href)
                new_a, new_t = self._resolve_assortment(old_a)
                if not new_a:
                    log.warning(f"    ✗ processingorder position {old_a} не смаплен")
                    continue
                positions.append({
                    "assortment": meta(f"{BASE}/entity/{new_t}/{new_a}", new_t),
                    "quantity": p.get("quantity", 1),
                })
            if positions:
                body["positions"] = positions

            # Атрибуты
            attrs = self._map_doc_attributes(
                full.get("attributes", []), "processingorder", attrs_index
            )
            if attrs:
                body["attributes"] = attrs

            created_uuid = None
            try:
                r = self.dst.post("entity/processingorder", body)
                created_uuid = uuid_from_href(r["meta"]["href"])
            except Exception as e:
                err = str(e)
                if "3006" in err and doc.get("name"):
                    ex = self._find_existing_doc("processingorder", doc["name"])
                    if ex:
                        self.umap.set("processingorder", old_uuid, ex, save=False)
                        created += 1
                        continue
                # 26001 — несоответствие позиций технкарте: пробуем без positions
                if "26001" in err and "positions" in body:
                    body_no_pos = {k: v for k, v in body.items() if k != "positions"}
                    try:
                        r2 = self.dst.post("entity/processingorder", body_no_pos)
                        created_uuid = uuid_from_href(r2["meta"]["href"])
                        log.info(f"  + processingorder/{doc.get('name')} (без positions)")
                    except Exception as e2:
                        log.warning(f"  ✗ processingorder/{doc.get('name','?')} (no-pos): {str(e2)[:200]}")
                else:
                    log.warning(f"  ✗ processingorder/{doc.get('name','?')}: {err[:250]}")

            if created_uuid:
                self.umap.set("processingorder", old_uuid, created_uuid, save=False)
                created += 1
                if created % 50 == 0:
                    self.umap.save()
                    log.info(f"  processingorder: {created}/{len(docs)}")
        self.umap.save()
        log.info(f"  processingorder: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Производство (processing) ─────────────────────────────────────

    def _map_processing_positions(self, rows: list) -> list:
        """Маппинг materials/products документа Производство."""
        result = []
        for pos in rows:
            assortment = pos.get("assortment") or {}
            a_href = assortment.get("meta", {}).get("href", "")
            old_uuid = uuid_from_href(a_href)
            new_uuid, new_type = self._map_assortment(old_uuid)
            if not new_uuid:
                log.warning(f"    ✗ processing assortment {old_uuid} не смаплен")
                continue
            result.append({
                "assortment": meta(f"{BASE}/entity/{new_type}/{new_uuid}", new_type),
                "quantity": pos.get("quantity", 1),
            })
        return result

    def migrate_processings(self):
        docs = self.src.get_all(
            "entity/processing",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Производство (processing): {len(docs)}")
        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("processing", old_uuid):
                skipped += 1
                continue
            # Обязательно expand — в списке materials/products пустые
            try:
                full = self.src.get(
                    f"entity/processing/{old_uuid}",
                    params={"expand": "materials.assortment,products.assortment"},
                )
            except Exception as e:
                log.warning(f"  ✗ get processing/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "quantity": full.get("quantity", 1),
                "externalCode": old_uuid,
                "processingSum": full.get("processingSum", 0),
            }

            # Организация
            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            # Склады
            for store_key in ("materialsStore", "productsStore"):
                s_href = (full.get(store_key) or {}).get("meta", {}).get("href", "")
                new_s = self.umap.get("store", uuid_from_href(s_href))
                if new_s:
                    body[store_key] = meta(f"{BASE}/entity/store/{new_s}", "store")

            # Техкарта
            plan_href = (full.get("processingPlan") or {}).get("meta", {}).get("href", "")
            new_plan = self.umap.get("processingplan", uuid_from_href(plan_href))
            if new_plan:
                body["processingPlan"] = meta(
                    f"{BASE}/entity/processingplan/{new_plan}", "processingplan"
                )

            # Заказ на производство
            po_href = (full.get("processingOrder") or {}).get("meta", {}).get("href", "")
            new_po = self.umap.get("processingorder", uuid_from_href(po_href))
            if new_po:
                body["processingOrder"] = meta(
                    f"{BASE}/entity/processingorder/{new_po}", "processingorder"
                )

            # Материалы и продукты
            mats = full.get("materials") or {}
            prods = full.get("products") or {}
            mats_rows = mats.get("rows", []) if isinstance(mats, dict) else []
            prods_rows = prods.get("rows", []) if isinstance(prods, dict) else []

            mapped_mats = self._map_processing_positions(mats_rows)
            mapped_prods = self._map_processing_positions(prods_rows)
            if mapped_mats:
                body["materials"] = mapped_mats
            if mapped_prods:
                body["products"] = mapped_prods

            try:
                r = self.dst.post("entity/processing", body)
                self.umap.set("processing", old_uuid, uuid_from_href(r["meta"]["href"]),
                               save=False)
                created += 1
                if created % 50 == 0:
                    self.umap.save()
                    log.info(f"  processing: {created}/{len(docs)}")
            except Exception as e:
                err = str(e)
                if "3006" in err and doc.get("name"):
                    ex = self._find_existing_doc("processing", doc["name"])
                    if ex:
                        self.umap.set("processing", old_uuid, ex, save=False)
                        created += 1
                        continue
                log.warning(f"  ✗ processing/{doc.get('name','?')}: {err[:250]}")
        self.umap.save()
        log.info(f"  processing: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Заказы поставщикам ────────────────────────────────────────────

    def migrate_purchase_orders(self):
        docs = self.src.get_all(
            "entity/purchaseorder",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Заказы поставщикам: {len(docs)}")
        try:
            old_attrs = self.src.get(
                "entity/purchaseorder/metadata/attributes"
            ).get("rows", [])
        except Exception:
            old_attrs = []
        attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("purchaseorder", old_uuid):
                skipped += 1
                continue
            try:
                full = self.src.get(
                    f"entity/purchaseorder/{old_uuid}",
                    params={"expand": "positions,attributes,state"},
                )
            except Exception as e:
                log.warning(f"  ✗ get purchaseorder/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "externalCode": old_uuid,
            }

            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            store_href = (full.get("store") or {}).get("meta", {}).get("href", "")
            new_store = self.umap.get("store", uuid_from_href(store_href))
            if new_store:
                body["store"] = meta(f"{BASE}/entity/store/{new_store}", "store")

            agent_href = (full.get("agent") or {}).get("meta", {}).get("href", "")
            agent_type = entity_type_from_href(agent_href)
            if agent_type == "counterparty":
                new_agent = self.umap.get("counterparty", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/counterparty/{new_agent}", "counterparty")
            elif agent_type == "organization":
                new_agent = self.umap.get("organization", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/organization/{new_agent}", "organization")

            state_data = full.get("state")
            if state_data:
                new_state_meta = self._ensure_state("purchaseorder", state_data)
                if new_state_meta:
                    body["state"] = new_state_meta

            pos_data = full.get("positions", {})
            pos_rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else []
            positions = self._map_positions(pos_rows)
            if positions:
                body["positions"] = positions

            attrs = self._map_doc_attributes(
                full.get("attributes", []), "purchaseorder", attrs_index
            )
            if attrs:
                body["attributes"] = attrs

            try:
                r = self.dst.post("entity/purchaseorder", body)
                self.umap.set("purchaseorder", old_uuid, uuid_from_href(r["meta"]["href"]),
                               save=False)
                created += 1
                if created % 50 == 0:
                    self.umap.save()
                    log.info(f"  purchaseorder: {created}/{len(docs)}")
            except Exception as e:
                err = str(e)
                if "3006" in err and doc.get("name"):
                    ex = self._find_existing_doc("purchaseorder", doc["name"])
                    if ex:
                        self.umap.set("purchaseorder", old_uuid, ex, save=False)
                        created += 1
                        continue
                log.warning(f"  ✗ purchaseorder/{doc.get('name','?')}: {err[:250]}")
        self.umap.save()
        log.info(f"  purchaseorder: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Связь Приёмки ↔ Заказы поставщикам ────────────────────────────

    def link_supply_purchase_orders(self):
        """PUT supply.purchaseOrder для всех смапленных приёмок."""
        supply_map = self.umap.data.get("supply", {})
        log.info(f"Связываю приёмки с заказами поставщикам: {len(supply_map)} шт")
        linked = skipped = 0
        for old_supply_uuid, new_supply_uuid in supply_map.items():
            try:
                old_doc = self.src.get(f"entity/supply/{old_supply_uuid}")
            except Exception as e:
                log.warning(f"  ✗ get supply/{old_supply_uuid}: {str(e)[:100]}")
                continue
            po_href = (old_doc.get("purchaseOrder") or {}).get("meta", {}).get("href", "")
            if not po_href:
                skipped += 1
                continue
            old_po_uuid = uuid_from_href(po_href)
            new_po_uuid = self.umap.get("purchaseorder", old_po_uuid)
            if not new_po_uuid:
                log.warning(f"  ✗ supply/{old_supply_uuid}: purchaseOrder {old_po_uuid} не смаплен")
                skipped += 1
                continue
            try:
                self.dst.put(
                    f"entity/supply/{new_supply_uuid}",
                    {"purchaseOrder": meta(
                        f"{BASE}/entity/purchaseorder/{new_po_uuid}", "purchaseorder"
                    )},
                )
                linked += 1
            except Exception as e:
                log.warning(f"  ✗ link supply/{new_supply_uuid}: {str(e)[:150]}")
        log.info(f"  Связано приёмок: {linked}, без заказа: {skipped}")

    # ── EXTRAS: Возвраты поставщикам ─────────────────────────────────────────

    def migrate_purchase_returns(self):
        docs = self.src.get_all(
            "entity/purchasereturn",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Возвраты поставщикам: {len(docs)}")
        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("purchasereturn", old_uuid):
                skipped += 1
                continue
            try:
                full = self.src.get(
                    f"entity/purchasereturn/{old_uuid}",
                    params={"expand": "positions"},
                )
            except Exception as e:
                log.warning(f"  ✗ get purchasereturn/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "externalCode": old_uuid,
            }

            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            store_href = (full.get("store") or {}).get("meta", {}).get("href", "")
            new_store = self.umap.get("store", uuid_from_href(store_href))
            if new_store:
                body["store"] = meta(f"{BASE}/entity/store/{new_store}", "store")

            agent_href = (full.get("agent") or {}).get("meta", {}).get("href", "")
            agent_type = entity_type_from_href(agent_href)
            new_agent = (
                self.umap.get("counterparty", uuid_from_href(agent_href))
                if agent_type == "counterparty"
                else self.umap.get("organization", uuid_from_href(agent_href))
            )
            if new_agent:
                body["agent"] = meta(
                    f"{BASE}/entity/{agent_type}/{new_agent}", agent_type
                )

            # Связь с приёмкой
            supply_href = (full.get("supply") or {}).get("meta", {}).get("href", "")
            if supply_href:
                new_supply = self.umap.get("supply", uuid_from_href(supply_href))
                if new_supply:
                    body["supply"] = meta(f"{BASE}/entity/supply/{new_supply}", "supply")

            pos_data = full.get("positions", {})
            pos_rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else []
            positions = self._map_positions(pos_rows)
            if positions:
                body["positions"] = positions

            try:
                r = self.dst.post("entity/purchasereturn", body)
                self.umap.set("purchasereturn", old_uuid, uuid_from_href(r["meta"]["href"]))
                created += 1
            except Exception as e:
                log.warning(f"  ✗ purchasereturn/{doc.get('name','?')}: {str(e)[:250]}")
        self.umap.save()
        log.info(f"  purchasereturn: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Счёт-фактуры выданные ─────────────────────────────────────────

    def migrate_facture_out(self):
        docs = self.src.get_all(
            "entity/factureout",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Счёт-фактуры выданные: {len(docs)}")
        try:
            old_attrs = self.src.get(
                "entity/factureout/metadata/attributes"
            ).get("rows", [])
        except Exception:
            old_attrs = []
        attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("factureout", old_uuid):
                skipped += 1
                continue
            try:
                full = self.src.get(
                    f"entity/factureout/{old_uuid}",
                    params={"expand": "attributes"},
                )
            except Exception as e:
                log.warning(f"  ✗ get factureout/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "externalCode": old_uuid,
            }
            if full.get("paymentDate"):
                body["paymentDate"] = full["paymentDate"]
            if full.get("paymentNumber"):
                body["paymentNumber"] = full["paymentNumber"]

            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            agent_href = (full.get("agent") or {}).get("meta", {}).get("href", "")
            agent_type = entity_type_from_href(agent_href)
            if agent_type == "counterparty":
                new_agent = self.umap.get("counterparty", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/counterparty/{new_agent}", "counterparty")
            elif agent_type == "organization":
                new_agent = self.umap.get("organization", uuid_from_href(agent_href))
                if new_agent:
                    body["agent"] = meta(f"{BASE}/entity/organization/{new_agent}", "organization")

            # Связанные отгрузки (demands)
            raw_demands = doc.get("demands", [])
            if not raw_demands:
                raw_demands = full.get("demands", [])
            new_demands = []
            for d in raw_demands:
                d_href = d.get("meta", {}).get("href", "")
                old_d = uuid_from_href(d_href)
                new_d = self.umap.get("demand", old_d)
                if new_d:
                    new_demands.append(meta(f"{BASE}/entity/demand/{new_d}", "demand"))
            if new_demands:
                body["demands"] = new_demands

            attrs = self._map_doc_attributes(
                full.get("attributes", []), "factureout", attrs_index
            )
            if attrs:
                body["attributes"] = attrs

            try:
                r = self.dst.post("entity/factureout", body)
                self.umap.set("factureout", old_uuid, uuid_from_href(r["meta"]["href"]),
                               save=False)
                created += 1
                if created % 50 == 0:
                    self.umap.save()
                    log.info(f"  factureout: {created}/{len(docs)}")
            except Exception as e:
                err = str(e)
                if "3006" in err and doc.get("name"):
                    ex = self._find_existing_doc("factureout", doc["name"])
                    if ex:
                        self.umap.set("factureout", old_uuid, ex, save=False)
                        created += 1
                        continue
                log.warning(f"  ✗ factureout/{doc.get('name','?')}: {err[:250]}")
        self.umap.save()
        log.info(f"  factureout: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Оприходования (enter) ─────────────────────────────────────────

    def migrate_enter_docs(self):
        docs = self.src.get_all(
            "entity/enter",
            params={"filter": f"moment>={YEAR_START}"},
        )
        log.info(f"Оприходования (enter): {len(docs)}")
        created = skipped = 0
        for doc in docs:
            old_uuid = uuid_from_href(doc["meta"]["href"])
            if self.umap.has("enter", old_uuid):
                skipped += 1
                continue
            try:
                full = self.src.get(
                    f"entity/enter/{old_uuid}",
                    params={"expand": "positions"},
                )
            except Exception as e:
                log.warning(f"  ✗ get enter/{old_uuid}: {str(e)[:150]}")
                continue

            body: dict = {
                "name": full.get("name"),
                "moment": full.get("moment"),
                "applicable": full.get("applicable", True),
                "externalCode": old_uuid,
            }
            if full.get("description"):
                body["description"] = full["description"]

            org_href = (full.get("organization") or {}).get("meta", {}).get("href", "")
            new_org = self.umap.get("organization", uuid_from_href(org_href))
            if new_org:
                body["organization"] = meta(f"{BASE}/entity/organization/{new_org}", "organization")

            store_href = (full.get("store") or {}).get("meta", {}).get("href", "")
            new_store = self.umap.get("store", uuid_from_href(store_href))
            if new_store:
                body["store"] = meta(f"{BASE}/entity/store/{new_store}", "store")

            pos_data = full.get("positions", {})
            pos_rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else []
            positions = self._map_positions(pos_rows)
            if positions:
                body["positions"] = positions

            try:
                r = self.dst.post("entity/enter", body)
                self.umap.set("enter", old_uuid, uuid_from_href(r["meta"]["href"]))
                created += 1
            except Exception as e:
                log.warning(f"  ✗ enter/{doc.get('name','?')}: {str(e)[:250]}")
        self.umap.save()
        log.info(f"  enter: создано={created}, пропущено={skipped}")

    # ── EXTRAS: Возвраты покупателей (заглушка) ───────────────────────────────

    def migrate_sales_returns(self):
        log.info("Возвраты покупателей (salesreturn): 0 за 2026 — пропуск")

    # ── EXTRAS: Верификация новых типов ───────────────────────────────────────

    def verify_extras(self):
        log.info("\n" + "=" * 60)
        log.info("ВЕРИФИКАЦИЯ EXTRAS (src vs dst, 2026)")
        log.info("=" * 60)
        checks = [
            ("entity/purchaseorder",   "purchaseorder",   True),
            ("entity/purchasereturn",  "purchasereturn",  True),
            ("entity/factureout",      "factureout",      True),
            ("entity/enter",           "enter",           True),
            ("entity/processingorder", "processingorder", True),
            ("entity/processing",      "processing",      True),
            ("entity/processingplan",  "processingplan",  False),
            ("entity/processingplanfolder", "processingplanfolder", False),
        ]
        params_year = {"limit": 1, "filter": f"moment>={YEAR_START}"}
        params_all  = {"limit": 1}
        for path, label, use_year in checks:
            p = params_year if use_year else params_all
            try:
                src_n = self.src.get(path, p).get("meta", {}).get("size", "?")
            except Exception:
                src_n = "ERR"
            try:
                dst_n = self.dst.get(path, p).get("meta", {}).get("size", "?")
            except Exception:
                dst_n = "ERR"
            mark = "OK" if src_n == dst_n else "DIFF"
            scope = "2026" if use_year else " all"
            log.info(f"  [{mark}] {label} ({scope}): src={src_n} → dst={dst_n}")

    # ── Главный запуск ────────────────────────────────────────────────────────

    def run(self):
        log.info(f"\n{'='*60}")
        mode = 'PILOT' if self.pilot else ('CATCHUP' if self.catchup else ('EXTRAS' if self.extras else 'FULL'))
        log.info(f"Старт миграции {mode} — {datetime.now()}")
        log.info(f"OLD: ...{OLD_TOKEN[-8:]} → NEW: ...{NEW_TOKEN[-8:]}")
        log.info(f"{'='*60}\n")

        if self.extras:
            steps = [
                ("E1. Атрибуты новых типов документов", self.migrate_extra_doc_attributes),
                ("E2. Папки техкарт", self.migrate_processing_plan_folders),
                ("E3. Техкарты (processingplan)", self.migrate_processing_plans),
                ("E4. Заказы на производство", self.migrate_processing_orders),
                ("E5. Производство (processing)", self.migrate_processings),
                ("E6. Заказы поставщикам", self.migrate_purchase_orders),
                ("E7. Связь приёмки ↔ заказы поставщикам", self.link_supply_purchase_orders),
                ("E8. Возвраты поставщикам", self.migrate_purchase_returns),
                ("E9. Счёт-фактуры выданные", self.migrate_facture_out),
                ("E10. Оприходования", self.migrate_enter_docs),
                ("E11. Возвраты покупателей", self.migrate_sales_returns),
                ("E12. Верификация extras", self.verify_extras),
            ]
        elif self.catchup:
            steps = [
                ("8. Контрагенты (новые)", self.migrate_counterparties),
                ("7a. Варианты (модификации)", self.migrate_variants),
                ("10. Документы 2026 (проход 1)", self.migrate_documents),
                ("10b. Архивированные продукты + variant", self.migrate_archived_products),
                ("10b2. Недостающие variant", self.migrate_missing_variants),
                ("10c. Документы 2026 (проход 2)", self.migrate_documents),
                ("11. Связи платежей", self.link_payments),
                ("12. Верификация", self.verify),
            ]
        else:
            steps = [
                ("1. Справочники customentity", self.migrate_custom_entities),
                ("2. Кастомные атрибуты документов", self.migrate_document_attributes),
                ("2.5. Кастомные атрибуты товаров/услуг", self.migrate_product_service_attrs),
                ("3. Организации", self.migrate_organizations),
                ("4. Единицы измерения", self.migrate_uom),
                ("5. Склады", self.migrate_stores),
                ("6. Папки номенклатуры", self.migrate_product_folders),
                ("7. Товары и услуги", self.migrate_products),
                ("7a. Варианты (модификации)", self.migrate_variants),
                ("7b. Патч атрибутов ранее созданных товаров", self.patch_product_attrs),
                ("8. Контрагенты", self.migrate_counterparties),
                ("9. Ввод остатков 01.01.2026", self.migrate_stock_balance),
                ("10. Документы 2026 (проход 1)", self.migrate_documents),
                ("10b. Архивированные продукты (из позиций упавших документов)", self.migrate_archived_products),
                ("10b2. Недостающие variant", self.migrate_missing_variants),
                ("10c. Документы 2026 (проход 2, дозаливка упавших)", self.migrate_documents),
                ("11. Связи платежей", self.link_payments),
                ("12. Верификация", self.verify),
            ]
        if self.pilot:
            steps.append(("13. Pilot URLs", self.pilot_report_urls))

        for name, fn in steps:
            log.info(f"\n{'─'*60}\n{name}\n{'─'*60}")
            try:
                fn()
            except KeyboardInterrupt:
                log.warning("\nПрервано пользователем. Маппинг сохранён.")
                self.umap.save()
                sys.exit(0)
            except Exception as e:
                log.error(f"Ошибка в шаге '{name}': {e}")
                self.umap.save()
                log.error("Маппинг сохранён. Перезапуск пропустит выполненные шаги.")
                raise

        log.info(f"\nЗавершено: {datetime.now()}")


def main():
    parser = argparse.ArgumentParser(description="Миграция МойСклад Wangpack → новый аккаунт")
    parser.add_argument("--pilot", action="store_true",
                        help="Pilot режим: ограниченные выборки для проверки")
    parser.add_argument("--catchup", action="store_true",
                        help="Catchup: только variant + документы + платежи (без справочников)")
    parser.add_argument("--extras", action="store_true",
                        help="Extras: производство, закупки, счёт-фактуры, оприходования")
    args = parser.parse_args()
    Migrator(pilot=args.pilot, catchup=args.catchup, extras=args.extras).run()


if __name__ == "__main__":
    main()
