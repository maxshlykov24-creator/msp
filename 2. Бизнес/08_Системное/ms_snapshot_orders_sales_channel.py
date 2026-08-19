#!/usr/bin/env python3
"""
Снимок заказов покупателя: UUID, номер (name), системное поле канала продаж.

В МойСклад это поле документа salesChannel (ссылка на справочник saleschannel),
а не дополнительный атрибут. Данные берутся из ответа API с expand=salesChannel.

Токен только из окружения (не хардкодить).

Запуск:
  MS_TOKEN=... python3 ms_snapshot_orders_sales_channel.py
  MS_TOKEN=... MS_CLIENT=OtherClient python3 ms_snapshot_orders_sales_channel.py
  MS_TOKEN=... python3 ms_snapshot_orders_sales_channel.py --client Detensor
  MS_TOKEN=... MS_SNAPSHOT_DB=/tmp/custom.sqlite python3 ms_snapshot_orders_sales_channel.py

Путь к БД по умолчанию: рядом со скриптом — clients/<Client>/data/orders_sales_channel.sqlite
Рядом с .sqlite пишется выгрузка .txt (UTF-8, TSV с заголовком).
Переопределение: переменная MS_SNAPSHOT_DB (полный путь к файлу).

Важно: expand=salesChannel при limit>100 часто не отдаёт id — пагинация limit=100.
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
DB_FILENAME = "orders_sales_channel.sqlite"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS orders_sales_channel (
  order_id TEXT PRIMARY KEY NOT NULL,
  order_name TEXT,
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
    override = os.environ.get("MS_SNAPSHOT_DB", "").strip()
    if override:
        return override
    data_dir = os.path.join(SCRIPT_DIR, "clients", client_slug, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, DB_FILENAME)


def fetch_all_orders():
    rows = []
    offset = 0
    while True:
        d = api_get(
            "/entity/customerorder",
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='orders_sales_channel'"
    )
    if not cur.fetchone():
        conn.executescript(CREATE_TABLE_SQL)
        conn.commit()
        return

    cols = {row[1] for row in conn.execute("PRAGMA table_info(orders_sales_channel)").fetchall()}
    if "sales_channel_id" in cols and "base_sales_channel_id" not in cols:
        return

    conn.execute("ALTER TABLE orders_sales_channel RENAME TO orders_sales_channel_legacy")
    conn.executescript(CREATE_TABLE_SQL)
    leg_cols = {row[1] for row in conn.execute("PRAGMA table_info(orders_sales_channel_legacy)").fetchall()}
    if "base_sales_channel_id" in leg_cols:
        conn.execute(
            """
            INSERT OR REPLACE INTO orders_sales_channel (
              order_id, order_name, sales_channel_id, sales_channel_name, snapshot_at
            )
            SELECT order_id, order_name, base_sales_channel_id, base_sales_channel_name, snapshot_at
            FROM orders_sales_channel_legacy
            """
        )
    conn.execute("DROP TABLE orders_sales_channel_legacy")
    conn.commit()


def upsert_batch(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO orders_sales_channel (
          order_id, order_name, sales_channel_id, sales_channel_name, snapshot_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
          order_name = excluded.order_name,
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
                "order_id",
                "order_name",
                "sales_channel_id",
                "sales_channel_name",
                "snapshot_at",
            ]
        )
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Снимок системного salesChannel по заказам МойСклад → SQLite"
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

    print("Загрузка всех заказов (expand=salesChannel, системное поле канала)...", flush=True)
    orders = fetch_all_orders()
    print(f"  Заказов: {len(orders)}", flush=True)

    snapshot_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    batch = []
    for o in orders:
        oid = o.get("id")
        if not oid:
            continue
        sc = o.get("salesChannel") or {}
        batch.append(
            (
                oid,
                o.get("name"),
                sc.get("id"),
                sc.get("name"),
                snapshot_at,
            )
        )

    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        upsert_batch(conn, batch)
    finally:
        conn.close()

    txt_path = txt_path_for_db(db_path)
    write_txt_export(txt_path, sorted(batch, key=lambda r: ((r[1] or ""), r[0])))
    print(f"Готово: {len(batch)} строк → {db_path}", flush=True)
    print(f"  TSV: {txt_path}", flush=True)


if __name__ == "__main__":
    main()
