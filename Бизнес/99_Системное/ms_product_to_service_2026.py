#!/usr/bin/env python3
"""
Миграция МойСклад (JSON API 1.2): по кодам товаров создать услуги (имя и salePrices с товара),
затем в документах 2026 года заменить позиции product → service.

Типы документов: customerorder, invoiceout, demand, supply (приёмка), enter (оприходование), loss (списание) — фильтр: --doc-types.
Фильтр периода: moment (календарный 2026), черновики включены (applicable не фильтруем).

Переменные окружения:
  MS_TOKEN — обязательно
  MS_OUTPUT_DIR — каталог отчётов/логов/маппинга по умолчанию (/tmp)
  MS_MAP — путь к JSON маппинга product_id -> service_id (по умолчанию MS_OUTPUT_DIR/ms_product_to_service_map.json)
  MS_RATE_LIMIT_MS — пауза между запросами (по умолчанию 150); при --workers N интервал для скана делится на N
  MS_SERVICE_FOLDER — имя группы товаров/услуг для новой услуги (по умолчанию «Услуги»; можно отменить: --no-service-folder)

Этапы:
  create-services — найти товары по коду, POST /entity/service, записать маппинг
  report — сколько документов/строк затронуто (без записи)
  fix — PUT документов с заменой assortment на service

Примеры:
  MS_TOKEN=... python3 ms_product_to_service_2026.py create-services --codes 03542 --map-file /tmp/m.json
  MS_TOKEN=... python3 ms_product_to_service_2026.py report --map-file /tmp/m.json --doc-types customerorder
  MS_TOKEN=... python3 ms_product_to_service_2026.py fix --map-file /tmp/m.json --doc-types customerorder
  Приёмка / оприходование / списание: --doc-types supply | enter loss
  Полный охват продаж: без --doc-types или --doc-types customerorder invoiceout demand
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import ChunkedEncodingError, ConnectTimeout, ReadTimeout

TOKEN = os.environ.get("MS_TOKEN", "")
API_BASE = "https://api.moysklad.ru/api/remap/1.2"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Content-Type": "application/json",
}
RATE_LIMIT_MS = int(os.environ.get("MS_RATE_LIMIT_MS", "150"))
# Делитель интервала при параллельном скане (ставится в scan_and_maybe_fix)
_scan_workers = 1

_thread_local = threading.local()


def http_session() -> requests.Session:
    """Session на поток: keep-alive к api.moysklad.ru (requests.Session не thread-safe)."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return s

FILTER_2026 = "moment>=2026-01-01 00:00:00;moment<=2026-12-31 23:59:59"

DOC_TYPES_DEFAULT = ("customerorder", "invoiceout", "demand")

# Согласованный список: колонка «Код» из МойСклад (ищем товар по article, затем по code)
DEFAULT_PRODUCT_CODES: Tuple[str, ...] = (
    "03542",
    "03572",
    "06173",
    "06213",
    "06348",
    "06429",
)


def map_path_default() -> str:
    base = os.environ.get("MS_OUTPUT_DIR", "/tmp")
    return os.environ.get("MS_MAP", os.path.join(base, "ms_product_to_service_map.json"))


def throttle() -> None:
    time.sleep(RATE_LIMIT_MS / 1000.0)


def throttle_scan() -> None:
    """Пауза перед GET полного документа; при нескольких воркерах интервал делится (агрегат нагрузки)."""
    d = max(1, _scan_workers)
    time.sleep((RATE_LIMIT_MS / 1000.0) / d)


def api_get(url_or_path: str, params=None) -> Any:
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(6):
        try:
            r = http_session().get(url, params=params, timeout=120)
        except (
            ReadTimeout,
            ConnectTimeout,
            requests.exceptions.ConnectionError,
            ChunkedEncodingError,
        ) as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 5:
                raise RuntimeError(f"GET сеть: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("GET failed")


def api_post(url_or_path: str, body: dict) -> Any:
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = http_session().post(url, json=body, timeout=120)
        except (
            ReadTimeout,
            ConnectTimeout,
            requests.exceptions.ConnectionError,
            ChunkedEncodingError,
        ) as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"POST сеть: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        if r.status_code == 412:
            err = r.json().get("errors", [{}])[0].get("error", "")
            raise RuntimeError(f"412: {err}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("POST failed")


def api_put(url_or_path: str, body: dict) -> Any:
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = http_session().put(url, json=body, timeout=120)
        except (
            ReadTimeout,
            ConnectTimeout,
            requests.exceptions.ConnectionError,
            ChunkedEncodingError,
        ) as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"PUT сеть: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        if r.status_code == 412:
            err = r.json().get("errors", [{}])[0].get("error", "")
            raise RuntimeError(f"412: {err}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("PUT failed")


def find_product_folder_by_name(name: str) -> Optional[dict]:
    """Группа номенклатуры (productFolder). При нескольких совпадениях по имени — первое с точным name."""
    throttle()
    data = api_get("/entity/productfolder", {"filter": f"name={name}", "limit": 100})
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return None
    for r in rows:
        if r.get("name") == name:
            return r
    return rows[0]


def assign_service_product_folder(service_id: str, folder: dict) -> None:
    """Поместить услугу в группу (поле productFolder)."""
    throttle()
    full = api_get(f"/entity/service/{service_id}")
    fmeta = folder.get("meta")
    if not isinstance(fmeta, dict):
        raise RuntimeError("У папки нет meta")
    body = {"meta": full["meta"], "productFolder": {"meta": fmeta}}
    throttle()
    api_put(f"/entity/service/{service_id}", body)


def find_product_by_code(code: str) -> Tuple[dict, str]:
    """Возвращает (product, поле_поиска). Несколько или ноль строк — исключение."""
    for field in ("article", "code"):
        throttle()
        data = api_get("/entity/product", {"filter": f"{field}={code}", "limit": 100})
        rows = data.get("rows") or []
        if len(rows) == 1:
            return rows[0], field
        if len(rows) > 1:
            raise RuntimeError(
                f"Неоднозначно: по {field}={code!r} найдено {len(rows)} товаров — уточните вручную"
            )
    raise RuntimeError(f"Товар с кодом {code!r} не найден (article и code)")


def service_meta(service_id: str) -> dict:
    return {
        "meta": {
            "href": f"{API_BASE}/entity/service/{service_id}",
            "metadataHref": f"{API_BASE}/entity/service/metadata",
            "type": "service",
            "mediaType": "application/json",
        }
    }


# Кэш variant_id -> product_id (на время одного процесса)
_VARIANT_PRODUCT_CACHE: Dict[str, str] = {}


def mapped_product_id_from_position(pos: dict) -> Optional[str]:
    """ID товара для сопоставления с маппингом: product в позиции или product у variant."""
    a = pos.get("assortment")
    if not isinstance(a, dict):
        return None
    meta = a.get("meta") or {}
    t = meta.get("type")
    href = (meta.get("href") or "").rstrip("/")
    if not href:
        return None
    eid = href.split("/")[-1]
    if t == "product":
        return eid
    if t == "variant":
        if eid in _VARIANT_PRODUCT_CACHE:
            return _VARIANT_PRODUCT_CACHE[eid]
        throttle()
        try:
            v = api_get(f"/entity/variant/{eid}")
        except Exception:
            return None
        prod = v.get("product")
        pid = prod.get("id") if isinstance(prod, dict) else None
        if pid:
            _VARIANT_PRODUCT_CACHE[eid] = pid
        return pid
    return None


def position_for_put(pos: dict, mapping: Dict[str, str]) -> dict:
    """Копия позиции для PUT: assortment только meta; при маппинге — service."""
    new_p = copy.deepcopy(pos)
    pid = mapped_product_id_from_position(pos)
    if pid and pid in mapping:
        new_p["assortment"] = service_meta(mapping[pid])
    else:
        a = new_p.get("assortment")
        if isinstance(a, dict) and "meta" in a:
            new_p["assortment"] = {"meta": a["meta"]}
    return new_p


def positions_need_replace(positions: List[dict], mapping: Dict[str, str]) -> int:
    n = 0
    for p in positions:
        pid = mapped_product_id_from_position(p)
        if pid and pid in mapping:
            n += 1
    return n


def normalize_positions(raw: Any) -> List[dict]:
    """В ответе API positions может быть list либо объект коллекции с полем rows."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if isinstance(raw, dict):
        rows = raw.get("rows")
        if isinstance(rows, list):
            return [p for p in rows if isinstance(p, dict)]
    return []


def strip_document_for_put(doc: dict) -> dict:
    """Убираем поля, которые часто мешают PUT (объекты-ссылки из expand)."""
    body = copy.deepcopy(doc)
    for k in (
        "created",
        "updated",
        "printed",
        "files",
        "paymentNumber",
    ):
        body.pop(k, None)
    return body


def fetch_docs_2026(entity: str) -> List[dict]:
    rows: List[dict] = []
    offset = 0
    while True:
        throttle()
        data = api_get(
            f"/entity/{entity}",
            {
                "filter": FILTER_2026,
                "limit": 100,
                "offset": offset,
            },
        )
        chunk = data.get("rows") or []
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
    return rows


def load_mapping(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Маппинг должен быть объектом JSON {product_id: service_id}")
    out: Dict[str, str] = {}
    for k, v in data.items():
        out[str(k)] = str(v)
    return out


def cmd_create_services(args: argparse.Namespace) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.map_file or map_path_default()
    codes = list(args.codes) if args.codes else list(DEFAULT_PRODUCT_CODES)

    mapping: Dict[str, str] = {}
    log_lines: List[str] = []

    if os.path.isfile(out_path) and not args.force:
        with open(out_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if isinstance(existing, dict):
            for k, v in existing.items():
                mapping[str(k)] = str(v)
            log_lines.append(f"Загружен существующий маппинг ({len(mapping)} записей) из {out_path}")

    for code in codes:
        throttle()
        product, field = find_product_by_code(code)
        pid = product["id"]
        pname = product.get("name", "?")

        if pid in mapping:
            log_lines.append(f"SKIP code={code} product={pid} уже в маппинге -> service {mapping[pid]}")
            continue

        body: Dict[str, Any] = {"name": pname}
        sp = product.get("salePrices")
        if sp:
            body["salePrices"] = copy.deepcopy(sp)

        throttle()
        try:
            created = api_post("/entity/service", body)
        except Exception as e:
            log_lines.append(f"FAIL POST service для товара {pid} code={code}: {e}")
            raise

        sid = created["id"]
        mapping[pid] = sid
        log_lines.append(
            f"OK code={code} по_полю={field} product={pid} ({pname!r}) -> service={sid}"
        )

        folder_name = getattr(args, "service_folder", None)
        if folder_name:
            try:
                pf = find_product_folder_by_name(folder_name)
                if not pf:
                    log_lines.append(
                        f"WARN папка {folder_name!r} не найдена (productFolder) — услуга {sid} без группы"
                    )
                else:
                    assign_service_product_folder(sid, pf)
                    pn = pf.get("pathName") or pf.get("name", "?")
                    log_lines.append(
                        f"OK service={sid} -> группа productFolder {folder_name!r} pathName={pn!r}"
                    )
            except Exception as e:
                log_lines.append(f"WARN не удалось поместить услугу {sid} в группу {folder_name!r}: {e}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
        f.write("\n")

    report_path = os.path.join(
        os.environ.get("MS_OUTPUT_DIR", "/tmp"), f"ms_product_to_service_create_{ts}.log"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print("\n".join(log_lines))
    print(f"\nМаппинг записан: {out_path}", flush=True)
    print(f"Лог: {report_path}", flush=True)


def _scan_one_document(entity: str, did: str, mapping: Dict[str, str]) -> Tuple[dict, List[dict], int, str]:
    """GET документа с позициями; throttle_scan снаружи по потокам."""
    full = api_get(
        f"/entity/{entity}/{did}",
        {"expand": "positions.assortment"},
    )
    positions = normalize_positions(full.get("positions"))
    n_lines = positions_need_replace(positions, mapping)
    name = full.get("name", "?")
    return full, positions, n_lines, name


def scan_and_maybe_fix(args: argparse.Namespace, fix: bool) -> None:
    global _scan_workers

    mpath = args.map_file or map_path_default()
    mapping = load_mapping(mpath)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.environ.get("MS_OUTPUT_DIR", "/tmp")

    total_docs = 0
    total_lines = 0
    to_fix: List[Tuple[str, str, str, int]] = []

    max_docs = args.max_docs
    scan_limit = getattr(args, "scan_limit", None)
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    _scan_workers = workers

    dt = getattr(args, "doc_types", None)
    doc_types: Tuple[str, ...] = tuple(dt) if dt else DOC_TYPES_DEFAULT

    def run_scan_batch(entity: str, docs_slice: List[dict]) -> List[Tuple[str, str, str, int, dict, List[dict]]]:
        """Список (entity, did, name, n_lines, full, positions) для строк с n_lines>0, порядок как в docs_slice."""

        def _task(item: Tuple[int, str]) -> Tuple[int, str, str, int, dict, List[dict]]:
            _idx, did = item
            throttle_scan()
            full, positions, n_lines, name = _scan_one_document(entity, did, mapping)
            return (_idx, did, name, n_lines, full, positions)

        indexed = [(i, d["id"]) for i, d in enumerate(docs_slice)]
        if not indexed:
            return []
        if workers == 1:
            raw = [_task(t) for t in indexed]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                raw = list(pool.map(_task, indexed))
        raw.sort(key=lambda x: x[0])
        out: List[Tuple[str, str, str, int, dict, List[dict]]] = []
        for _idx, did, name, n_lines, full, positions in raw:
            if n_lines <= 0:
                continue
            out.append((entity, did, name, n_lines, full, positions))
        return out

    print(f"Типы документов: {', '.join(doc_types)}", flush=True)

    for entity in doc_types:
        throttle()
        print(f"Список {entity} за 2026...", flush=True)
        docs = fetch_docs_2026(entity)
        print(f"  всего: {len(docs)}", flush=True)
        print(f"  скан: workers={workers} MS_RATE_LIMIT_MS={RATE_LIMIT_MS}", flush=True)

        if scan_limit is not None:
            docs_slice = docs[: scan_limit]
        else:
            docs_slice = docs

        batch = run_scan_batch(entity, docs_slice)
        n_entity = 0
        seen = 0
        for entity_b, did, name, n_lines, full, positions in batch:
            if max_docs is not None and seen >= max_docs:
                break
            seen += 1
            n_entity += 1
            total_docs += 1
            total_lines += n_lines
            to_fix.append((entity_b, did, name, n_lines))
            if not fix:
                continue

            def _put_doc() -> None:
                new_positions = [position_for_put(p, mapping) for p in positions]
                body = strip_document_for_put(full)
                body["positions"] = new_positions
                throttle()
                try:
                    api_put(f"/entity/{entity_b}/{did}", body)
                except RuntimeError as e:
                    if "412" in str(e):
                        full2 = api_get(
                            f"/entity/{entity_b}/{did}",
                            {"expand": "positions.assortment"},
                        )
                        new_positions2 = [
                            position_for_put(p, mapping)
                            for p in normalize_positions(full2.get("positions"))
                        ]
                        body2 = strip_document_for_put(full2)
                        body2["positions"] = new_positions2
                        throttle()
                        api_put(f"/entity/{entity_b}/{did}", body2)
                    else:
                        raise

            try:
                _put_doc()
            except Exception as e:
                err_path = os.path.join(out_dir, f"ms_product_to_service_fix_errors_{ts}.log")
                with open(err_path, "a", encoding="utf-8") as ef:
                    ef.write(f"FAIL {entity_b} {did} {name!r}: {e!s}\n")
                print(f"  ОШИБКА {entity_b} {did}: {e}", flush=True)

        print(f"  затронуто документов (с нашими товарами): {n_entity}", flush=True)

    _scan_workers = 1

    summary_path = os.path.join(
        out_dir,
        f"ms_product_to_service_{'fix' if fix else 'report'}_{ts}.txt",
    )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"Режим: {'fix' if fix else 'report'}\n")
        f.write(f"Типы документов: {', '.join(doc_types)}\n")
        f.write(f"Маппинг: {mpath}\n")
        f.write(f"Документов к обработке: {total_docs}\n")
        f.write(f"Строк позиций (product из списка): {total_lines}\n\n")
        for entity, did, name, nl in to_fix:
            f.write(f"{entity}\t{name}\tid={did}\tlines={nl}\n")

    print(f"\nИтого документов: {total_docs}, строк позиций: {total_lines}", flush=True)
    print(f"Отчёт: {summary_path}", flush=True)

    if not fix:
        print("Для применения: python3 ... fix (и при необходимости уберите --max-docs)", flush=True)
        return

    log_path = os.path.join(out_dir, f"ms_product_to_service_fix_{ts}.log")
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"OK записано через scan_and_maybe_fix total_docs={total_docs}\n")
    print(f"Лог fix: {log_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Товары → услуги и замена в документах 2026")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-services", help="Создать услуги и JSON маппинга")
    p_create.add_argument(
        "--map-file",
        default=None,
        help="Куда записать маппинг (по умолчанию MS_MAP или MS_OUTPUT_DIR/ms_product_to_service_map.json)",
    )
    p_create.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="Коды товаров (по умолчанию встроенный список из 6 позиций)",
    )
    p_create.add_argument(
        "--force",
        action="store_true",
        help="Не подмешивать существующий файл маппинга (перезаписать с нуля по успешным create)",
    )
    p_create.add_argument(
        "--service-folder",
        default=None,
        metavar="NAME",
        help="Имя группы (productFolder), куда поместить созданную услугу. По умолчанию: env MS_SERVICE_FOLDER или «Услуги»",
    )
    p_create.add_argument(
        "--no-service-folder",
        action="store_true",
        help="Не назначать группу после создания услуги",
    )

    p_rep = sub.add_parser("report", help="Отчёт по документам 2026 без записи")
    p_rep.add_argument("--map-file", default=None, help="Путь к JSON маппинга")
    p_rep.add_argument(
        "--max-docs",
        type=int,
        default=None,
        help="Не более N документов с совпадениями на тип (пилот)",
    )
    p_rep.add_argument(
        "--scan-limit",
        type=int,
        default=None,
        metavar="N",
        help="Проверить не более N документов списка за 2026 на каждый тип (ускорение; без флага — все)",
    )
    p_rep.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Параллельных потоков для GET документов (1 = по очереди; 3–6 быстрее, при 429 уменьшить или поднять MS_RATE_LIMIT_MS)",
    )
    p_rep.add_argument(
        "--doc-types",
        nargs="+",
        choices=["customerorder", "invoiceout", "demand", "supply", "enter", "loss"],
        default=None,
        metavar="TYPE",
        help="Какие типы обходить (по умолчанию все три продажных). Примеры: supply, enter loss",
    )

    p_fix = sub.add_parser("fix", help="Заменить позиции в документах")
    p_fix.add_argument("--map-file", default=None, help="Путь к JSON маппинга")
    p_fix.add_argument(
        "--max-docs",
        type=int,
        default=None,
        help="Не более N документов с совпадениями на тип (пилот)",
    )
    p_fix.add_argument(
        "--scan-limit",
        type=int,
        default=None,
        metavar="N",
        help="Проверить не более N документов списка за 2026 на каждый тип (ускорение; без флага — все)",
    )
    p_fix.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Параллельных потоков для GET документов (см. report --workers)",
    )
    p_fix.add_argument(
        "--doc-types",
        nargs="+",
        choices=["customerorder", "invoiceout", "demand", "supply", "enter", "loss"],
        default=None,
        metavar="TYPE",
        help="Какие типы обходить (по умолчанию все три продажных). См. report --doc-types",
    )

    args = parser.parse_args()

    if not TOKEN:
        print("Задайте MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    if args.command == "create-services":
        if args.no_service_folder:
            args.service_folder = None
        elif args.service_folder is None:
            args.service_folder = os.environ.get("MS_SERVICE_FOLDER") or "Услуги"
        cmd_create_services(args)
    elif args.command == "report":
        scan_and_maybe_fix(args, fix=False)
    elif args.command == "fix":
        scan_and_maybe_fix(args, fix=True)
    else:
        parser.error("Неизвестная команда")


if __name__ == "__main__":
    main()
