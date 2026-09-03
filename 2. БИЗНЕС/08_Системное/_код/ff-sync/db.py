import fcntl
import os
import sqlite3
from contextlib import contextmanager

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def db_path():
    return os.environ.get("FF_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ff.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _cols(conn, table):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def migrate(conn):
    clients = _cols(conn, "clients")
    for col, decl in (
        ("tariff_storage", "REAL"),
        ("tariff_intake", "REAL"),
        ("tariff_ship", "REAL"),
    ):
        if col not in clients:
            conn.execute("ALTER TABLE clients ADD COLUMN %s %s" % (col, decl))
    cabs = _cols(conn, "cabinets")
    if "last_pull_at" not in cabs:
        conn.execute("ALTER TABLE cabinets ADD COLUMN last_pull_at TEXT")
    lots = _cols(conn, "lots")
    for col in ("billed_days", "billed_in", "billed_out"):
        if col not in lots:
            conn.execute("ALTER TABLE lots ADD COLUMN %s REAL NOT NULL DEFAULT 0" % col)


def init_db():
    conn = connect()
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        conn.executescript(f.read())
    migrate(conn)
    conn.commit()
    conn.close()
    return db_path()


@contextmanager
def run_lock(name="ff", blocking=True):
    path = os.path.join(os.path.dirname(db_path()), "%s.lock" % name)
    fh = open(path, "a+")
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fh.fileno(), flags)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def tables():
    conn = connect()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_client(code):
    conn = connect()
    row = conn.execute("SELECT * FROM clients WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row


def get_client_by_name(name):
    text = (name or "").strip()
    if not text:
        return None
    conn = connect()
    row = conn.execute(
        "SELECT * FROM clients WHERE lower(trim(name)) = lower(?)",
        (text,),
    ).fetchone()
    conn.close()
    return row


def get_client_by_ms_id(ms_id):
    if not ms_id:
        return None
    conn = connect()
    row = conn.execute("SELECT * FROM clients WHERE ms_counterparty_id = ?", (ms_id,)).fetchone()
    conn.close()
    return row


def code_taken(code):
    return get_client(code) is not None


def get_client_by_id(cid):
    conn = connect()
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return row


def insert_client(code, name, ms_counterparty_id, ms_org_id, ms_store_id):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO clients (code, name, ms_counterparty_id, ms_org_id, ms_store_id, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (code, name, ms_counterparty_id, ms_org_id, ms_store_id),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def update_client(cid, **fields):
    allowed = {
        "name",
        "ms_counterparty_id",
        "ms_org_id",
        "ms_store_id",
        "tariff_storage",
        "tariff_intake",
        "tariff_ship",
        "active",
    }
    sets = []
    vals = []
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append("%s = ?" % key)
        vals.append(val)
    if not sets:
        return
    vals.append(cid)
    conn = connect()
    conn.execute("UPDATE clients SET %s WHERE id = ?" % ", ".join(sets), vals)
    conn.commit()
    conn.close()


def insert_cabinet(client_id, marketplace, name, token, client_id_ext, active, last_ok_at, last_error):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO cabinets (client_id, marketplace, name, token, client_id_ext, active, last_ok_at, last_error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (client_id, marketplace, name, token, client_id_ext, active, last_ok_at, last_error),
    )
    conn.commit()
    cab_id = cur.lastrowid
    conn.close()
    return cab_id


def get_cabinet_by_client_mp(client_id, marketplace):
    conn = connect()
    row = conn.execute(
        "SELECT * FROM cabinets WHERE client_id = ? AND marketplace = ? ORDER BY id LIMIT 1",
        (client_id, marketplace),
    ).fetchone()
    conn.close()
    return row


def update_cabinet(cab_id, **fields):
    allowed = {
        "name",
        "token",
        "client_id_ext",
        "active",
        "last_ok_at",
        "last_error",
        "last_pull_at",
    }
    sets = []
    vals = []
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append("%s = ?" % key)
        vals.append(val)
    if not sets:
        return
    vals.append(cab_id)
    conn = connect()
    conn.execute("UPDATE cabinets SET %s WHERE id = ?" % ", ".join(sets), vals)
    conn.commit()
    conn.close()


def upsert_cabinet(client_id, marketplace, name, token, client_id_ext, active, last_ok_at, last_error):
    existing = get_cabinet_by_client_mp(client_id, marketplace)
    if existing:
        patch = {
            "name": name,
            "client_id_ext": client_id_ext,
            "active": active,
            "last_ok_at": last_ok_at,
            "last_error": last_error,
        }
        if token:
            patch["token"] = token
        update_cabinet(existing["id"], **patch)
        return existing["id"]
    return insert_cabinet(client_id, marketplace, name, token, client_id_ext, active, last_ok_at, last_error)


def list_clients():
    conn = connect()
    rows = conn.execute("SELECT * FROM clients ORDER BY id").fetchall()
    conn.close()
    return rows


def get_cabinet(cabinet_id):
    conn = connect()
    row = conn.execute(
        "SELECT cabinets.*, clients.code AS client_code FROM cabinets "
        "JOIN clients ON clients.id = cabinets.client_id WHERE cabinets.id = ?",
        (cabinet_id,),
    ).fetchone()
    conn.close()
    return row


def update_client_org(code, org_id):
    conn = connect()
    conn.execute("UPDATE clients SET ms_org_id = ? WHERE code = ?", (org_id, code))
    conn.commit()
    conn.close()


def set_setting(key, value):
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_setting(key):
    conn = connect()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def get_sku(cabinet_id, ext_key):
    conn = connect()
    row = conn.execute(
        "SELECT * FROM sku_map WHERE cabinet_id = ? AND ext_key = ?",
        (cabinet_id, ext_key),
    ).fetchone()
    conn.close()
    return row


def get_sku_by_barcode(client_id, barcode):
    norm = barcode_norm(barcode)
    if not norm:
        return None
    conn = connect()
    row = conn.execute(
        "SELECT * FROM sku_map WHERE client_id = ? AND ext_barcode = ? AND ms_product_id IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (client_id, barcode),
    ).fetchone()
    if not row and norm != barcode:
        row = conn.execute(
            "SELECT * FROM sku_map WHERE client_id = ? AND ext_barcode = ? AND ms_product_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (client_id, norm),
        ).fetchone()
    conn.close()
    return row


def upsert_sku(client_id, cabinet_id, marketplace, ext_key, ext_article, ext_barcode, ms_product_id):
    conn = connect()
    existing = conn.execute(
        "SELECT id FROM sku_map WHERE cabinet_id = ? AND ext_key = ?",
        (cabinet_id, ext_key),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE sku_map SET ext_article=?, ext_barcode=?, ms_product_id=? WHERE id=?",
            (ext_article, ext_barcode, ms_product_id, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO sku_map (client_id, cabinet_id, marketplace, ext_key, ext_article, ext_barcode, ms_product_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, cabinet_id, marketplace, ext_key, ext_article, ext_barcode, ms_product_id),
        )
    conn.commit()
    conn.close()


def barcode_norm(raw):
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.upper().startswith("OZN"):
        return s.upper()
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or s


def replace_cache(client_id, cabinet_id, rows):
    conn = connect()
    conn.execute("DELETE FROM catalog_cache WHERE cabinet_id = ?", (cabinet_id,))
    conn.executemany(
        "INSERT INTO catalog_cache (client_id, cabinet_id, marketplace, ext_key, ext_article, "
        "ext_barcode, barcode_norm, name, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                client_id,
                cabinet_id,
                row.get("marketplace") or "",
                str(row.get("ext_key") or ""),
                row.get("ext_article") or "",
                row.get("ext_barcode") or "",
                barcode_norm(row.get("ext_barcode")),
                row.get("name") or "",
                row.get("size") or "",
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()


def find_cache(client_id, barcode=None, article=None):
    conn = connect()
    rows = []
    if barcode:
        rows = conn.execute(
            "SELECT * FROM catalog_cache WHERE client_id = ? AND barcode_norm = ?",
            (client_id, barcode_norm(barcode)),
        ).fetchall()
    if not rows and article:
        art = str(article).strip()
        rows = conn.execute(
            "SELECT * FROM catalog_cache WHERE client_id = ? AND ext_article = ?",
            (client_id, art),
        ).fetchall()
    conn.close()
    return rows


def cache_count(client_id=None, cabinet_id=None):
    conn = connect()
    if cabinet_id:
        n = conn.execute("SELECT COUNT(*) AS n FROM catalog_cache WHERE cabinet_id = ?", (cabinet_id,)).fetchone()["n"]
    elif client_id:
        n = conn.execute("SELECT COUNT(*) AS n FROM catalog_cache WHERE client_id = ?", (client_id,)).fetchone()["n"]
    else:
        n = conn.execute("SELECT COUNT(*) AS n FROM catalog_cache").fetchone()["n"]
    conn.close()
    return n


def list_cabinets():
    conn = connect()
    rows = conn.execute(
        "SELECT cabinets.*, clients.code AS client_code FROM cabinets "
        "JOIN clients ON clients.id = cabinets.client_id ORDER BY cabinets.id"
    ).fetchall()
    conn.close()
    return rows


def insert_lot(client_id, ms_product_id, article, barcode, gtin, name, tracking_type, liters, qty, received_at, ms_supply_id):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO lots (client_id, ms_product_id, article, barcode, gtin, name, tracking_type, "
        "liters, qty_in, qty_left, received_at, ms_supply_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
            ms_product_id,
            article,
            barcode,
            gtin,
            name,
            tracking_type,
            liters,
            qty,
            qty,
            received_at,
            ms_supply_id,
        ),
    )
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return lid


def list_lots(only_open=False):
    conn = connect()
    if only_open:
        rows = conn.execute("SELECT * FROM lots WHERE qty_left > 0 ORDER BY received_at, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM lots ORDER BY received_at, id").fetchall()
    conn.close()
    return rows


def list_lots_fifo(client_id, ms_product_id):
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM lots WHERE client_id = ? AND ms_product_id = ? AND qty_left > 0 "
        "ORDER BY received_at, id",
        (client_id, ms_product_id),
    ).fetchall()
    conn.close()
    return rows


def add_lot_move(lot_id, qty, kind, ref, created_at):
    conn = connect()
    conn.execute(
        "UPDATE lots SET qty_left = qty_left - ? WHERE id = ?",
        (qty, lot_id),
    )
    conn.execute(
        "INSERT INTO lot_moves (lot_id, qty, kind, ref, created_at) VALUES (?, ?, ?, ?, ?)",
        (lot_id, qty, kind, ref, created_at),
    )
    conn.commit()
    conn.close()


def shipped_qty(lot_id):
    conn = connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(qty), 0) AS n FROM lot_moves WHERE lot_id = ? AND kind = 'ship'",
        (lot_id,),
    ).fetchone()
    conn.close()
    return float(row["n"] or 0)


def get_lot(lot_id):
    conn = connect()
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    conn.close()
    return row


def mark_lot_billed(lot_id, days, qty_in, qty_out):
    conn = connect()
    conn.execute(
        "UPDATE lots SET billed_days = ?, billed_in = ?, billed_out = ? WHERE id = ?",
        (days, qty_in, qty_out, lot_id),
    )
    conn.commit()
    conn.close()


def add_intake_row(client_id, fields, created_at, author):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO intake_queue (client_id, barcode, article, name, marketplace, gtin, "
        "tracking_type, liters, qty, state, note, created_at, author) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
            fields.get("barcode") or "",
            fields.get("article") or "",
            fields.get("name") or "",
            fields.get("marketplace") or "",
            fields.get("gtin") or "",
            fields.get("tracking_type") or "",
            fields.get("liters"),
            fields.get("qty") or 1,
            fields.get("state") or "draft",
            fields.get("note") or "",
            created_at,
            author or "",
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def update_intake_row(row_id, **fields):
    allowed = {
        "barcode",
        "article",
        "name",
        "marketplace",
        "gtin",
        "tracking_type",
        "liters",
        "qty",
        "state",
        "note",
        "ms_product_id",
        "ms_order_name",
    }
    sets = []
    vals = []
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append("%s = ?" % key)
        vals.append(val)
    if not sets:
        return
    vals.append(row_id)
    conn = connect()
    conn.execute("UPDATE intake_queue SET %s WHERE id = ?" % ", ".join(sets), vals)
    conn.commit()
    conn.close()


def delete_intake_row(row_id):
    conn = connect()
    conn.execute("DELETE FROM intake_queue WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def list_intake(states=("draft", "warn")):
    marks = ",".join("?" for _ in states)
    conn = connect()
    rows = conn.execute(
        "SELECT intake_queue.*, clients.name AS client_name FROM intake_queue "
        "JOIN clients ON clients.id = intake_queue.client_id "
        "WHERE intake_queue.state IN (%s) ORDER BY intake_queue.id" % marks,
        tuple(states),
    ).fetchall()
    conn.close()
    return rows


def insert_invoice(client_id, ms_invoice_id, ms_number, storage, intake, ship, total, lots_count, created_at, author):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO invoices (client_id, ms_invoice_id, ms_number, storage, intake, ship, total, "
        "lots_count, created_at, author) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (client_id, ms_invoice_id, ms_number, storage, intake, ship, total, lots_count, created_at, author),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def add_invoice_lot(invoice_id, lot_id, storage, intake, ship):
    conn = connect()
    conn.execute(
        "INSERT INTO invoice_lots (invoice_id, lot_id, storage, intake, ship) VALUES (?, ?, ?, ?, ?)",
        (invoice_id, lot_id, storage, intake, ship),
    )
    conn.commit()
    conn.close()


def list_invoices(limit=100):
    conn = connect()
    rows = conn.execute(
        "SELECT invoices.*, clients.name AS client_name FROM invoices "
        "JOIN clients ON clients.id = invoices.client_id ORDER BY invoices.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_order_log(cabinet_id, ext_order_id):
    conn = connect()
    row = conn.execute(
        "SELECT * FROM orders_log WHERE cabinet_id = ? AND ext_order_id = ?",
        (cabinet_id, ext_order_id),
    ).fetchone()
    conn.close()
    return row


def upsert_order_log(cabinet_id, ext_order_id, ms_order_id, result, error, created_at):
    conn = connect()
    existing = conn.execute(
        "SELECT id FROM orders_log WHERE cabinet_id = ? AND ext_order_id = ?",
        (cabinet_id, ext_order_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE orders_log SET ms_order_id=?, result=?, error=?, created_at=? WHERE id=?",
            (ms_order_id, result, error, created_at, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO orders_log (cabinet_id, ext_order_id, ms_order_id, result, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cabinet_id, ext_order_id, ms_order_id, result, error, created_at),
        )
    conn.commit()
    conn.close()
