#!/usr/bin/env python3
"""
Нормализация системного salesChannel в МойСклад для заказов (customerorder) и отгрузок (demand).

Правило по текущему имени канала (без учёта регистра, порядок важен):
  1) содержит «Партнеры» → канал «Партнеры»
  2) иначе «ОПТ» или «опт» → «ОПТ»
  3) иначе «Розница» → «Розница»
Иначе документ не меняется.

Целевые каналы в справочнике должны называться точно: Розница, ОПТ, Партнеры.

Локальные снимки в clients/*/data/*.sqlite и *.txt не трогаем — это архив для отката.

Запуск:
  MS_TOKEN=... python3 ms_normalize_sales_channels_moysklad.py --mode report
  MS_TOKEN=... python3 ms_normalize_sales_channels_moysklad.py --mode apply
  MS_TOKEN=... python3 ms_normalize_sales_channels_moysklad.py --mode report --only-orders
  Сначала только ОПТ: --only-target ОПТ (остальные классы пропускаются, в справочнике нужен только этот канал)

Переменные: MS_OUTPUT_DIR (каталог для логов, по умолчанию /tmp).
  MS_APPLY_DELAY — пауза в секундах после каждого PUT (по умолчанию 0.08; при 429 увеличьте).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import requests
from requests.exceptions import ConnectTimeout, ReadTimeout

TOKEN = os.environ.get("MS_TOKEN", "")
API_BASE = "https://api.moysklad.ru/api/remap/1.2"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Content-Type": "application/json",
}
RATE_LIMIT_S = float(os.environ.get("MS_APPLY_DELAY", "0.08"))

TARGET_EXACT_NAMES = ("Партнеры", "ОПТ", "Розница")
TARGET_CHOICES = list(TARGET_EXACT_NAMES)


def api_get(url_or_path, params=None):
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(6):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=120)
        except (ReadTimeout, ConnectTimeout) as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 5:
                raise RuntimeError(f"GET timeout: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("GET failed")


def api_put(url_or_path, body):
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = requests.put(url, headers=HEADERS, json=body, timeout=120)
        except (ReadTimeout, ConnectTimeout) as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"PUT timeout: {e}") from e
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


def fetch_sales_channels(required_names: tuple[str, ...]) -> dict[str, dict]:
    """Имя канала (точное) -> объект из API. required_names — какие имена должны быть в справочнике."""
    rows = []
    offset = 0
    while True:
        d = api_get("/entity/saleschannel", {"limit": 100, "offset": offset})
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
        time.sleep(0.25)
    by_name: dict[str, dict] = {}
    for row in rows:
        name = row.get("name")
        if name in TARGET_EXACT_NAMES:
            if name in by_name:
                raise RuntimeError(f"Дубликат канала с именем «{name}» в справочнике saleschannel")
            by_name[name] = row
    missing = [n for n in required_names if n not in by_name]
    if missing:
        raise RuntimeError(
            "В справочнике saleschannel не найдены каналы с точными именами: "
            + ", ".join(missing)
        )
    return by_name


def classify_from_current_name(name: str | None) -> str | None:
    if not name or not isinstance(name, str):
        return None
    n = name.lower()
    if "партнеры" in n:
        return "Партнеры"
    if "опт" in n:
        return "ОПТ"
    if "розница" in n:
        return "Розница"
    return None


def sales_channel_payload(ch: dict) -> dict:
    """Поля ссылки для PUT (как в ответах API)."""
    out: dict = {"meta": ch["meta"]}
    if ch.get("name") is not None:
        out["name"] = ch["name"]
    if ch.get("id") is not None:
        out["id"] = ch["id"]
    return out


def fetch_documents(path: str, label: str):
    rows = []
    offset = 0
    while True:
        d = api_get(
            path,
            {
                "limit": 100,
                "offset": offset,
                "expand": "salesChannel",
            },
        )
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
        time.sleep(0.25)
    return rows


def process_documents(
    *,
    docs: list[dict],
    entity_segment: str,
    channels_by_name: dict[str, dict],
    mode: str,
    log_file,
    only_target: str | None,
) -> dict:
    stats = {
        "total": len(docs),
        "no_sales_channel": 0,
        "no_rule": 0,
        "skipped_other_bucket": 0,
        "already_ok": 0,
        "to_update": 0,
        "applied_ok": 0,
        "applied_err": 0,
    }
    samples: list[str] = []

    for doc in docs:
        oid = doc.get("id")
        if not oid:
            continue
        sc = doc.get("salesChannel")
        if not sc or not sc.get("name"):
            stats["no_sales_channel"] += 1
            continue
        target_label = classify_from_current_name(sc.get("name"))
        if not target_label:
            stats["no_rule"] += 1
            continue
        if only_target and target_label != only_target:
            stats["skipped_other_bucket"] += 1
            continue
        target = channels_by_name[target_label]
        tid = target.get("id")
        cur_id = sc.get("id")
        if cur_id == tid:
            stats["already_ok"] += 1
            continue
        stats["to_update"] += 1
        name_doc = doc.get("name", "?")
        line = (
            f"{entity_segment}\t{oid}\t{name_doc}\t{sc.get('name')!r}\t->\t{target_label}\t({tid})"
        )
        if len(samples) < 25:
            samples.append(line)

        if mode == "apply":
            href = doc.get("meta", {}).get("href")
            if not href:
                stats["applied_err"] += 1
                log_file.write(f"FAIL no meta.href {oid}\n")
                log_file.flush()
                continue
            try:
                full = api_get(href, {"expand": "salesChannel"})
                time.sleep(RATE_LIMIT_S)
                body = {"meta": full["meta"], "salesChannel": sales_channel_payload(target)}
            except Exception as e:
                stats["applied_err"] += 1
                log_file.write(f"FAIL GET\t{line}\t{e}\n")
                log_file.flush()
                continue
            try:
                api_put(href, body)
                stats["applied_ok"] += 1
                log_file.write(f"OK\t{line}\n")
                log_file.flush()
            except RuntimeError as e:
                if "412" in str(e):
                    try:
                        full = api_get(href, {"expand": "salesChannel"})
                        time.sleep(RATE_LIMIT_S)
                        body = {"meta": full["meta"], "salesChannel": sales_channel_payload(target)}
                        api_put(href, body)
                        stats["applied_ok"] += 1
                        log_file.write(f"OK retry412\t{line}\n")
                        log_file.flush()
                    except Exception as e2:
                        stats["applied_err"] += 1
                        log_file.write(f"FAIL\t{line}\t{e2}\n")
                        log_file.flush()
                else:
                    stats["applied_err"] += 1
                    log_file.write(f"FAIL\t{line}\t{e}\n")
                    log_file.flush()
            time.sleep(RATE_LIMIT_S)

    for s in samples:
        print(f"  пример: {s}", flush=True)
        log_file.write(s + "\n")
    log_file.write(f"stats {entity_segment}: {stats}\n")
    log_file.flush()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Нормализация salesChannel в МойСклад")
    parser.add_argument("--mode", choices=["report", "apply"], required=True)
    parser.add_argument("--only-orders", action="store_true", help="Только customerorder")
    parser.add_argument("--only-demands", action="store_true", help="Только demand")
    parser.add_argument(
        "--only-target",
        choices=TARGET_CHOICES,
        default=None,
        metavar="КАНАЛ",
        help="Только документы, относящиеся к этому целевому каналу (классификация по имени). "
        "Пример: --only-target ОПТ — розница и партнёры не трогаем.",
    )
    args = parser.parse_args()

    if args.only_orders and args.only_demands:
        print("Нельзя одновременно --only-orders и --only-demands", file=sys.stderr)
        sys.exit(2)

    if not TOKEN:
        print("Задайте MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None

    out_dir = os.environ.get("MS_OUTPUT_DIR", "/tmp")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tgt = args.only_target or "all"
    log_path = os.path.join(out_dir, f"ms_normalize_sales_channel_{args.mode}_{tgt}_{ts}.log")

    required_channel_names: tuple[str, ...] = (
        (args.only_target,) if args.only_target else TARGET_EXACT_NAMES
    )
    print("Загрузка справочника saleschannel...", flush=True)
    channels_by_name = fetch_sales_channels(required_channel_names)
    for n in required_channel_names:
        print(f"  «{n}» id={channels_by_name[n].get('id')}", flush=True)

    do_orders = not args.only_demands
    do_demands = not args.only_orders

    all_stats: dict[str, dict] = {}

    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"mode={args.mode}\nonly_target={args.only_target}\n\n")

        if do_orders:
            print("\nЗаказы покупателя (customerorder)...", flush=True)
            orders = fetch_documents("/entity/customerorder", "order")
            print(f"  всего документов: {len(orders)}", flush=True)
            st = process_documents(
                docs=orders,
                entity_segment="customerorder",
                channels_by_name=channels_by_name,
                mode=args.mode,
                log_file=log,
                only_target=args.only_target,
            )
            all_stats["customerorder"] = st
            for k, v in st.items():
                print(f"  {k}: {v}", flush=True)

        if do_demands:
            print("\nОтгрузки (demand)...", flush=True)
            demands = fetch_documents("/entity/demand", "demand")
            print(f"  всего документов: {len(demands)}", flush=True)
            st = process_documents(
                docs=demands,
                entity_segment="demand",
                channels_by_name=channels_by_name,
                mode=args.mode,
                log_file=log,
                only_target=args.only_target,
            )
            all_stats["demand"] = st
            for k, v in st.items():
                print(f"  {k}: {v}", flush=True)

        log.write("\n" + repr(all_stats) + "\n")

    print(f"\nЛог: {log_path}", flush=True)
    if args.mode == "report":
        print("Для применения изменений: --mode apply", flush=True)
    else:
        print("DONE apply", flush=True)


if __name__ == "__main__":
    main()
