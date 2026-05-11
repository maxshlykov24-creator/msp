#!/usr/bin/env python3
"""
Снимок отгрузок (demand): UUID, номер (name), системное поле канала продаж salesChannel.

Токен только из окружения (не хардкодить).

Запуск:
  MS_TOKEN=... python3 ms_snapshot_demands_sales_channel.py
  MS_TOKEN=... python3 ms_snapshot_demands_sales_channel.py --client Detensor
  MS_TOKEN=... MS_SNAPSHOT_DEMANDS_DB=/tmp/demands.sqlite python3 ms_snapshot_demands_sales_channel.py

Путь к БД по умолчанию: clients/<Client>/data/demands_sales_channel.sqlite
Рядом пишется demands_sales_channel.txt (UTF-8, TSV).

Переопределение пути: MS_SNAPSHOT_DEMANDS_DB (не путать с MS_SNAPSHOT_DB для заказов).

Важно: expand=salesChannel — пагинация limit=100.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests
from requests.exceptions import ConnectTimeout, ReadTimeout

TOKEN = os.environ.get("MS_TOKEN", "")
API_BASE = "https://api.moysklad.ru/api/remap/1.2"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Content-Type": "application/json",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CLIENT = "Detensor"
DB_FILENAME = "demands_sales_channel.sqlite"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS demands_sales_channel (
  demand_id TEXT PRIMARY KEY NOT NULL,
  demand_name TEXT,
  sales_channel_id TEXT,
  sales_channel_name TEXT,
  snapshot_at TEXT NOT NULL
);
"""


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


def resolve_db_path(client_slug: str) -> str:
    override = os.environ.get("MS_SNAPSHOT_DEMANDS_DB", "").strip()
    if override:
        return override
    data_dir = os.path.join(SCRIPT_DIR, "clients", client_slug, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, DB_FILENAME)


def fetch_all_demands():
    rows = []
    offset = 0
    while True:
        d = api_get(
            "/entity/demand",
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


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(CREATE_TABLE_SQL)
    conn.commit()


def upsert_batch(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO demands_sales_channel (
          demand_id, demand_name, sales_channel_id, sales_channel_name, snapshot_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(demand_id) DO UPDATE SET
          demand_name = excluded.demand_name,
          sales_channel_id = excluded.sales_channel_id,
          sales_channel_name = excluded.sales_channel_name,
          snapshot_at = excluded.snapshot_at
        """,
        rows,
    )
    conn.commit()


def txt_path_for_db(db_path: str) -> str:
    root, _ = os.path.splitext(db_path)
    return f"{root}.txt"


def write_txt_export(txt_path: str, rows: list[tuple]) -> None:
    with open(txt_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(
            [
                "demand_id",
                "demand_name",
                "sales_channel_id",
                "sales_channel_name",
                "snapshot_at",
            ]
        )
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Снимок системного salesChannel по отгрузкам МойСклад → SQLite + TSV"
    )
    parser.add_argument(
        "--client",
        default=os.environ.get("MS_CLIENT", DEFAULT_CLIENT).strip() or DEFAULT_CLIENT,
        help=f"Имя папки клиента под clients/ (по умолчанию env MS_CLIENT или {DEFAULT_CLIENT})",
    )
    args = parser.parse_args()

    if not TOKEN:
        print("Задайте MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    client_slug = args.client.strip()
    if not client_slug:
        print("Пустой --client / MS_CLIENT", file=sys.stderr)
        sys.exit(1)

    db_path = resolve_db_path(client_slug)
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None

    print("Загрузка всех отгрузок demand (expand=salesChannel)...", flush=True)
    demands = fetch_all_demands()
    print(f"  Отгрузок: {len(demands)}", flush=True)

    snapshot_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    batch = []
    for doc in demands:
        did = doc.get("id")
        if not did:
            continue
        sc = doc.get("salesChannel") or {}
        batch.append(
            (
                did,
                doc.get("name"),
                sc.get("id"),
                sc.get("name"),
                snapshot_at,
            )
        )

    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        upsert_batch(conn, batch)
    finally:
        conn.close()

    txt_path = txt_path_for_db(db_path)
    write_txt_export(txt_path, sorted(batch, key=lambda r: ((r[1] or ""), r[0])))
    print(f"Готово: {len(batch)} строк → {db_path}", flush=True)
    print(f"  TSV: {txt_path}", flush=True)


if __name__ == "__main__":
    main()
