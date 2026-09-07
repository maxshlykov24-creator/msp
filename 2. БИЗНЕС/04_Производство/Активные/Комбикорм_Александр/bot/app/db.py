"""SQLite: схема, подключение, помощники по товароучёту.

Источник правды — таблица ``movements`` (неизменяемый журнал движений).
Остаток по позиции — сумма ``qty_kg`` её движений. Внутренняя единица — кг.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    brand         TEXT,
    code          TEXT,
    variant       TEXT,
    line          TEXT,
    bag_size_kg   REAL,
    price_bag     REAL,
    price_kg      REAL,
    cost          REAL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS aliases (
    id         INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    alias_norm TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'catalog',
    UNIQUE(product_id, alias_norm)
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON aliases(alias_norm);

CREATE TABLE IF NOT EXISTS allowed_users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    registered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS movements (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    type         TEXT NOT NULL CHECK(type IN ('sale','receipt','writeoff','inv_adj')),
    product_id   INTEGER NOT NULL REFERENCES products(id),
    qty_kg       REAL NOT NULL,
    unit_spoken  TEXT NOT NULL DEFAULT 'bag' CHECK(unit_spoken IN ('bag','kg')),
    price        REAL,
    note         TEXT,
    raw_text     TEXT,
    session_id   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_movements_product ON movements(product_id);
CREATE INDEX IF NOT EXISTS idx_movements_session ON movements(session_id, type);

CREATE TABLE IF NOT EXISTS sales_sessions (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    date          TEXT NOT NULL,
    total_spoken  REAL,
    discount      REAL NOT NULL DEFAULT 0,
    revenue       REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'confirmed'
);

CREATE TABLE IF NOT EXISTS sale_lines (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sales_sessions(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    qty_kg      REAL NOT NULL,
    unit_spoken TEXT NOT NULL DEFAULT 'bag',
    price       REAL,
    line_sum    REAL
);

CREATE TABLE IF NOT EXISTS daily_money (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL UNIQUE,
    cash         REAL NOT NULL DEFAULT 0,
    transfer     REAL NOT NULL DEFAULT 0,
    expense      REAL NOT NULL DEFAULT 0,
    closing_cash REAL,
    bot_revenue  REAL,
    status       TEXT,
    created_ts   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory_sessions (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    date    TEXT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS inventory_lines (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES inventory_sessions(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    expected_kg REAL NOT NULL DEFAULT 0,
    counted_kg  REAL NOT NULL DEFAULT 0,
    diff_kg     REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --- whitelist -----------------------------------------------------------

def is_allowed(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None


def try_register_user(conn: sqlite3.Connection, user_id: int, username: str, limit: int) -> bool:
    """Регистрирует пользователя, если есть свободный слот. Возвращает True при успехе/если уже был."""
    if is_allowed(conn, user_id):
        return True
    count = conn.execute("SELECT COUNT(*) FROM allowed_users").fetchone()[0]
    if count >= limit:
        return False
    with transaction(conn):
        conn.execute(
            "INSERT INTO allowed_users(user_id, username) VALUES(?, ?)",
            (user_id, username or ""),
        )
    return True


# --- каталог -------------------------------------------------------------

def product_by_id(conn: sqlite3.Connection, product_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def active_products(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM products WHERE active = 1 ORDER BY canonical_name"
    ).fetchall()


def aliases_for_matching(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.product_id, a.alias_norm, p.canonical_name, p.bag_size_kg,
               p.price_bag, p.price_kg
        FROM aliases a JOIN products p ON p.id = a.product_id
        WHERE p.active = 1
        """
    ).fetchall()


def add_alias(conn: sqlite3.Connection, product_id: int, alias_norm: str, source: str = "learned") -> None:
    if not alias_norm:
        return
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO aliases(product_id, alias_norm, source) VALUES(?, ?, ?)",
            (product_id, alias_norm, source),
        )


# --- движения и остаток --------------------------------------------------

def record_movement(
    conn: sqlite3.Connection,
    *,
    type_: str,
    product_id: int,
    qty_kg: float,
    unit_spoken: str = "bag",
    price: Optional[float] = None,
    note: Optional[str] = None,
    raw_text: Optional[str] = None,
    session_id: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO movements(type, product_id, qty_kg, unit_spoken, price, note, raw_text, session_id)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (type_, product_id, qty_kg, unit_spoken, price, note, raw_text, session_id),
    )
    return int(cur.lastrowid)


def stock_kg(conn: sqlite3.Connection, product_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(qty_kg), 0) FROM movements WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    return float(row[0] or 0)


def all_stock(conn: sqlite3.Connection) -> list[dict]:
    """Остаток по всем активным позициям (кг + производные мешки)."""
    rows = conn.execute(
        """
        SELECT p.id, p.canonical_name, p.bag_size_kg, p.price_bag, p.price_kg, p.cost,
               COALESCE(SUM(m.qty_kg), 0) AS qty_kg
        FROM products p
        LEFT JOIN movements m ON m.product_id = p.id
        WHERE p.active = 1
        GROUP BY p.id
        ORDER BY p.canonical_name
        """
    ).fetchall()
    result = []
    for r in rows:
        bag = r["bag_size_kg"] or 0
        qty = float(r["qty_kg"] or 0)
        result.append(
            {
                "id": r["id"],
                "name": r["canonical_name"],
                "bag_size_kg": bag,
                "qty_kg": qty,
                "bags": (qty / bag) if bag else None,
                "price_bag": r["price_bag"],
                "cost": r["cost"],
            }
        )
    return result


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def bulk_insert_products(conn: sqlite3.Connection, products: Iterable[dict]) -> None:
    with transaction(conn):
        for p in products:
            cur = conn.execute(
                """
                INSERT INTO products
                    (canonical_name, brand, code, variant, line, bag_size_kg,
                     price_bag, price_kg, cost, active)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    p["canonical_name"], p.get("brand"), p.get("code"),
                    p.get("variant"), p.get("line"), p.get("bag_size_kg"),
                    p.get("price_bag"), p.get("price_kg"), p.get("cost"),
                    1 if p.get("active", True) else 0,
                ),
            )
            pid = int(cur.lastrowid)
            for alias in p.get("aliases", []):
                conn.execute(
                    "INSERT OR IGNORE INTO aliases(product_id, alias_norm, source) VALUES(?,?,?)",
                    (pid, alias, "catalog"),
                )
