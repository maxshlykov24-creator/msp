import fcntl
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

SHIP_KEEP_DAYS = 14


def ship_keep_since():
    """Нижняя граница списка заказов: полночь 14 дней назад."""
    return (datetime.now() - timedelta(days=SHIP_KEEP_DAYS)).strftime("%Y-%m-%d 00:00")


def prune_old_shipments(days=SHIP_KEEP_DAYS):
    """Убираем отправления старше окна синка — иначе «Заказы» копят историю."""
    cut = (datetime.now() - timedelta(days=int(days or SHIP_KEEP_DAYS))).strftime("%Y-%m-%d 00:00")
    when = "replace(coalesce(nullif(accepted_at,''), shipped_at), 'T', ' ')"
    conn = connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM shipments WHERE %s < ?" % when, (cut,)).fetchall()]
    if ids:
        idq = ",".join("?" * len(ids))
        conn.execute("DELETE FROM shipment_marks WHERE shipment_id IN (%s)" % idq, ids)
        conn.execute("DELETE FROM shipments WHERE id IN (%s)" % idq, ids)
        conn.commit()
    conn.close()
    return len(ids)

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


def _rewrite_wb_dropoff(conn):
    """Снять с заданий WB адрес сдачи, выдуманный по габариту.

    До 11.09 колонка «Куда везти» заполнялась по cargoType: малогабарит — ПВЗ
    Домодедовская 28. Это оказалось догадкой: точку сдачи задаёт только ЛК, и
    поставка с тем же габаритом спокойно уезжала в СЦ. Теперь адрес приходит от
    поставки, а у заданий вне поставки он пустой — честнее, чем врущая надпись.
    """
    if "shipments" not in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    cols = _cols(conn, "shipments")
    if "office" not in cols or "supply_ext" not in cols:
        return
    conn.execute(
        "UPDATE shipments SET office = '' "
        "WHERE marketplace = 'wb' AND kind = 'fbs' "
        "AND COALESCE(office, '') <> '' AND COALESCE(supply_ext, '') = ''"
    )


def migrate(conn):
    clients = _cols(conn, "clients")
    for col, decl in (
        ("tariff_storage", "REAL"),
        ("tariff_intake", "REAL"),
        ("tariff_ship", "REAL"),
        ("tariff_pick", "REAL"),
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
    if "tariff" not in lots:
        conn.execute("ALTER TABLE lots ADD COLUMN tariff REAL")
    if "dims" not in lots:
        conn.execute("ALTER TABLE lots ADD COLUMN dims TEXT")
    if "pick_rate" not in lots:
        # колонка tariff была ставкой хранения; ставка теперь общая, а у позиции
        # своя цена сборки. Старые значения не переносим: 0,15 ₽ за штуку — не сборка.
        conn.execute("ALTER TABLE lots ADD COLUMN pick_rate REAL")
    if "accepted_at" not in lots:
        # счётчик стартует от фактической приёмки. Партии, заведённые до этого
        # правила, уже лежат на складе: считаем принятыми в день заведения,
        # иначе прошлые счёта разъедутся с новыми.
        conn.execute("ALTER TABLE lots ADD COLUMN accepted_at TEXT")
        conn.execute("UPDATE lots SET accepted_at = received_at WHERE accepted_at IS NULL")
    if "billed_until" not in lots:
        # billed_days считал дни от прихода: закрыты сутки [received; received + billed_days - 1]
        conn.execute("ALTER TABLE lots ADD COLUMN billed_until TEXT")
        conn.execute(
            "UPDATE lots SET billed_until = date(substr(received_at, 1, 10), "
            "'+' || (CAST(billed_days AS INTEGER) - 1) || ' days') "
            "WHERE billed_days > 0 AND billed_until IS NULL"
        )
    queue = _cols(conn, "intake_queue")
    if "supply_id" not in queue:
        conn.execute("ALTER TABLE intake_queue ADD COLUMN supply_id INTEGER")
    if "tariff" not in queue:
        conn.execute("ALTER TABLE intake_queue ADD COLUMN tariff REAL")
    if "pick_rate" not in queue:
        conn.execute("ALTER TABLE intake_queue ADD COLUMN pick_rate REAL")
    if "dims" not in queue:
        conn.execute("ALTER TABLE intake_queue ADD COLUMN dims TEXT")
        # раньше количество не спрашивали и в партию уходила единица: обнуляем, чтобы Глеб вписал
        conn.execute("UPDATE intake_queue SET qty = 0 WHERE state IN ('draft', 'warn')")
        conn.execute(
            "UPDATE intake_queue SET state = 'warn', note = 'нужно количество' "
            "WHERE state IN ('draft', 'warn') AND note NOT LIKE 'нет в кабинетах%'"
        )
    invoices = _cols(conn, "invoices")
    for col in ("period_from", "period_to"):
        if col not in invoices:
            conn.execute("ALTER TABLE invoices ADD COLUMN %s TEXT" % col)
    inv_lots = _cols(conn, "invoice_lots")
    for col in ("days", "liter_days"):
        if col not in inv_lots:
            conn.execute("ALTER TABLE invoice_lots ADD COLUMN %s REAL NOT NULL DEFAULT 0" % col)
    for col in ("period_from", "period_to"):
        if col not in inv_lots:
            conn.execute("ALTER TABLE invoice_lots ADD COLUMN %s TEXT" % col)
    cache = _cols(conn, "catalog_cache")
    for col, decl in (
        ("gtin", "TEXT"),
        ("tracking_type", "TEXT"),
        ("subject", "TEXT"),
        ("need_kiz", "INTEGER"),
        ("image", "TEXT"),
        # бренд и цвет нужны только этикетке товара: склад клеит их по образцу
        ("brand", "TEXT"),
        ("color", "TEXT"),
    ):
        if col not in cache:
            conn.execute("ALTER TABLE catalog_cache ADD COLUMN %s %s" % (col, decl))
    ships = _cols(conn, "shipments")
    fresh = "status_group" not in ships
    for col in (
        "status_group", "work_state", "accepted_at", "deadline_at", "track", "warehouse", "image",
        "supply_ext", "trbx_ext",
        # куда везти задание и его габаритный тип: от них зависит,
        # ПВЗ это или сортировочный центр и нужны ли грузоместа
        "office", "cargo_type", "pickup_allowed",
    ):
        if col not in ships:
            conn.execute("ALTER TABLE shipments ADD COLUMN %s TEXT" % col)
    supplies = _cols(conn, "wb_supplies")
    if "cargo_type" not in supplies:
        conn.execute("ALTER TABLE wb_supplies ADD COLUMN cargo_type TEXT")
    if "pickup_allowed" not in _cols(conn, "wb_supplies"):
        conn.execute("ALTER TABLE wb_supplies ADD COLUMN pickup_allowed TEXT")
    if "shipping_point" not in _cols(conn, "wb_supplies"):
        # выбранная в ЛК точка ПВЗ: единственный честный признак, что поставка
        # поедет не в СЦ. Флаг pickup_allowed бывает true и без выбранной точки
        conn.execute("ALTER TABLE wb_supplies ADD COLUMN shipping_point TEXT")
        # до 11.09 флаг ставился по габариту, а не читался с карточки: у всех
        # малогабаритных поставок стояло '1', включая уехавшие в СЦ. Значение
        # было догадкой, поэтому обнуляем — факт придёт от площадки при сверке
        conn.execute("UPDATE wb_supplies SET pickup_allowed = ''")
    if "shipping_dt" not in _cols(conn, "wb_supplies"):
        # дату отгрузки WB требует в том же запросе, что и точку сдачи
        conn.execute("ALTER TABLE wb_supplies ADD COLUMN shipping_dt TEXT")
    _rewrite_wb_dropoff(conn)
    if fresh:
        # у записей до появления раздела «Сборка» группы нет, и они не попали бы
        # ни на одну вкладку. Разбираем её из сохранённого текста статуса, чтобы
        # раздел не выглядел пустым до первой новой выгрузки.
        import statuses

        rows = conn.execute("SELECT id, status, shipped_at FROM shipments").fetchall()
        conn.executemany(
            "UPDATE shipments SET status_group = ?, accepted_at = ? WHERE id = ?",
            [(statuses.group_from_text(r["status"]), r["shipped_at"] or "", r["id"]) for r in rows],
        )
        if rows:
            print("сборка: проставил группу статуса для %s прошлых отправлений" % len(rows))


def init_db():
    conn = connect()
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        conn.executescript(f.read())
    migrate(conn)
    conn.commit()
    conn.close()
    return db_path()


def lock_name(client_id=None):
    """Имя замка прохода: у каждого контрагента свой.

    Один общий замок означал, что кнопка склада ждёт, пока воркер догрузит чужого
    клиента, а круг по всем кабинетам идёт минутами.
    """
    return "ff-client-%s" % int(client_id) if client_id else "ff"


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
        "tariff_pick",
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
        "ext_barcode, barcode_norm, name, size, brand, color, gtin, tracking_type, subject, need_kiz, image) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                row.get("brand") or "",
                row.get("color") or "",
                row.get("gtin") or "",
                row.get("tracking_type") or "",
                row.get("subject") or "",
                row.get("need_kiz"),
                row.get("image") or "",
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()


def catalog_card(client_id, barcode=None, article=None):
    """Карточка товара из кэша каталога: штрихкод, GTIN, размер, бренд, цвет.

    Нужна там, где в самом отправлении этих полей нет. У Ozon выгрузка заказов
    штрихкод не отдаёт вообще, а размер, бренд и цвет живут только в каталоге
    кабинета — без них этикетка товара не сходится с образцом склада.
    """
    if not client_id:
        return {}
    hits = find_cache(client_id, barcode=barcode or None, article=article or None)
    if not hits:
        return {}
    best = prefer_hit(hits)[0]
    have = set(best.keys())
    out = {}
    for key, col in (
        ("barcode", "ext_barcode"),
        ("gtin", "gtin"),
        ("name", "name"),
        ("size", "size"),
        ("brand", "brand"),
        ("color", "color"),
    ):
        out[key] = (best[col] or "") if col in have else ""
    return out


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


def list_cache(client_id):
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM catalog_cache WHERE client_id = ? ORDER BY id",
        (client_id,),
    ).fetchall()
    conn.close()
    return rows


def find_cache_group(client_id, barcode=None, article=None):
    """Один SKU на нескольких площадках: штрихкод, GTIN или ключ размера / оффера.

    Артикул в группу не входит: у WB один артикул на все размеры.
    Разные EAN и разные размеры — разные товары.
    """
    seed = find_cache(client_id, barcode=barcode) if barcode else []
    if not seed:
        return find_cache(client_id, article=article) if article else []
    all_rows = list_cache(client_id)
    ids = {row["id"] for row in seed}
    keys = {(row["marketplace"], row["ext_key"]) for row in seed if row["ext_key"]}
    gtins = {row["gtin"] for row in seed if row["gtin"]}
    norms = {row["barcode_norm"] for row in seed if row["barcode_norm"]}
    changed = True
    while changed:
        changed = False
        for row in all_rows:
            if row["id"] in ids:
                continue
            take = False
            if row["gtin"] and row["gtin"] in gtins:
                take = True
            elif row["barcode_norm"] and row["barcode_norm"] in norms:
                take = True
            elif row["ext_key"] and (row["marketplace"], row["ext_key"]) in keys:
                take = True
            if not take:
                continue
            ids.add(row["id"])
            if row["ext_key"]:
                keys.add((row["marketplace"], row["ext_key"]))
            if row["gtin"]:
                gtins.add(row["gtin"])
            if row["barcode_norm"]:
                norms.add(row["barcode_norm"])
            changed = True
    return [row for row in all_rows if row["id"] in ids]


def prefer_hit(hits):
    """WB с нормальным EAN важнее внутреннего кода и OZN."""
    def score(row):
        code = barcode_norm(row["ext_barcode"] or "")
        ean = 2 if code.isdigit() and len(code) in (8, 13) and not code.startswith("2") else 0
        wb = 1 if row["marketplace"] == "wb" else 0
        return (ean, wb, 1 if row["name"] else 0)
    return sorted(hits, key=score, reverse=True)


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


def insert_lot(client_id, ms_product_id, article, barcode, gtin, name, tracking_type, liters, qty, received_at, ms_supply_id, pick_rate=None, dims="", accepted_at=None):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO lots (client_id, ms_product_id, article, barcode, gtin, name, tracking_type, "
        "liters, dims, pick_rate, qty_in, qty_left, received_at, accepted_at, ms_supply_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
            ms_product_id,
            article,
            barcode,
            gtin,
            name,
            tracking_type,
            liters,
            dims or "",
            pick_rate,
            qty,
            qty,
            received_at,
            accepted_at,
            ms_supply_id,
        ),
    )
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return lid


def list_lots(only_open=False):
    """only_open прячет партию, только когда она вывезена И хранение закрыто счётом.

    Вывезенная партия ещё должна деньги за дни, что лежала до отгрузки.
    """
    conn = connect()
    if only_open:
        rows = conn.execute(
            "SELECT * FROM lots WHERE qty_left > 0 OR billed_until IS NULL "
            "OR billed_until < COALESCE((SELECT max(substr(created_at, 1, 10)) "
            "FROM lot_moves WHERE lot_moves.lot_id = lots.id), substr(received_at, 1, 10)) "
            "ORDER BY received_at, id"
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM lots ORDER BY received_at, id").fetchall()
    conn.close()
    return rows


def lots_awaiting_accept():
    """Партии, которые ещё не встали на счётчик: приёмки в МойСклад не видели."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM lots WHERE (accepted_at IS NULL OR accepted_at = '') "
        "AND ms_supply_id IS NOT NULL AND ms_supply_id <> '' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def mark_lot_accepted(lot_id, accepted_at):
    conn = connect()
    conn.execute(
        "UPDATE lots SET accepted_at = ? WHERE id = ? AND (accepted_at IS NULL OR accepted_at = '')",
        (accepted_at, int(lot_id)),
    )
    conn.commit()
    conn.close()


def set_lot_pick_rate(lot_id, rate):
    conn = connect()
    conn.execute("UPDATE lots SET pick_rate = ? WHERE id = ?", (rate, int(lot_id)))
    conn.commit()
    conn.close()


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


def moves_by_day(lot_id=None):
    """{lot_id: {'ГГГГ-ММ-ДД': шт}} — сколько уехало в каждый день."""
    conn = connect()
    sql = (
        "SELECT lot_id, substr(created_at, 1, 10) AS day, SUM(qty) AS qty FROM lot_moves "
        "WHERE kind = 'ship'"
    )
    args = []
    if lot_id is not None:
        sql += " AND lot_id = ?"
        args.append(int(lot_id))
    rows = conn.execute(sql + " GROUP BY lot_id, day", args).fetchall()
    conn.close()
    out = {}
    for row in rows:
        out.setdefault(row["lot_id"], {})[row["day"]] = float(row["qty"] or 0)
    return out


def get_lot(lot_id):
    conn = connect()
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    conn.close()
    return row


def mark_lot_billed(lot_id, billed_until, days, qty_in, qty_out):
    """Двигаем границу вперёд, назад — никогда: иначе дни уйдут в счёт дважды."""
    conn = connect()
    conn.execute(
        "UPDATE lots SET billed_until = CASE "
        "WHEN billed_until IS NULL OR billed_until < ? THEN ? ELSE billed_until END, "
        "billed_days = ?, billed_in = ?, billed_out = ? WHERE id = ?",
        (billed_until, billed_until, days, qty_in, qty_out, lot_id),
    )
    conn.commit()
    conn.close()


SUPPLY_FIELDS = (
    "number",
    "client_name",
    "contract",
    "planned_at",
    "contact",
    "carrier",
    "car_plate",
    "places",
    "marking",
    "terms",
)


def insert_supply(client_id, supply, source_name, rows_total, qty_total, created_at, author):
    cols = ["client_id"] + list(SUPPLY_FIELDS) + [
        "source_name",
        "rows_total",
        "qty_total",
        "created_at",
        "author",
    ]
    vals = [client_id] + [str(supply.get(k) or "") for k in SUPPLY_FIELDS] + [
        source_name or "",
        rows_total,
        qty_total,
        created_at,
        author or "",
    ]
    conn = connect()
    cur = conn.execute(
        "INSERT INTO intake_supplies (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" for _ in cols)),
        vals,
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def delete_supply(supply_id):
    conn = connect()
    conn.execute("DELETE FROM intake_supplies WHERE id = ?", (supply_id,))
    conn.commit()
    conn.close()


def get_supply(supply_id):
    conn = connect()
    row = conn.execute(
        "SELECT intake_supplies.*, clients.name AS client FROM intake_supplies "
        "JOIN clients ON clients.id = intake_supplies.client_id WHERE intake_supplies.id = ?",
        (supply_id,),
    ).fetchone()
    conn.close()
    return row


def list_supplies(client_id=None, limit=50):
    conn = connect()
    sql = (
        "SELECT intake_supplies.*, clients.name AS client, "
        "(SELECT COUNT(*) FROM intake_queue q WHERE q.supply_id = intake_supplies.id "
        "AND q.state IN ('draft', 'warn', 'clash')) AS open_rows "
        "FROM intake_supplies JOIN clients ON clients.id = intake_supplies.client_id"
    )
    args = []
    if client_id:
        sql += " WHERE intake_supplies.client_id = ?"
        args.append(client_id)
    sql += " ORDER BY intake_supplies.id DESC LIMIT ?"
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def add_intake_row(client_id, fields, created_at, author, supply_id=None):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO intake_queue (client_id, supply_id, barcode, article, name, marketplace, gtin, "
        "tracking_type, liters, dims, pick_rate, qty, state, note, created_at, author) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
            supply_id,
            fields.get("barcode") or "",
            fields.get("article") or "",
            fields.get("name") or "",
            fields.get("marketplace") or "",
            fields.get("gtin") or "",
            fields.get("tracking_type") or "",
            fields.get("liters"),
            fields.get("dims") or "",
            fields.get("pick_rate"),
            fields.get("qty") or 0,
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
        "dims",
        "pick_rate",
        "qty",
        "state",
        "note",
        "ms_product_id",
        "ms_order_name",
        "supply_id",
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


# Строки очереди, которые ещё в работе. 'clash' — артикул завёл на несколько
# карточек: такую строку в МойСклад не пускаем, пока оператор не выберет товар.
QUEUE_STATES = ("draft", "warn", "clash")


def list_intake(states=("draft", "warn")):
    marks = ",".join("?" for _ in states)
    conn = connect()
    rows = conn.execute(
        "SELECT intake_queue.*, clients.name AS client_name, "
        "intake_supplies.number AS supply_number FROM intake_queue "
        "JOIN clients ON clients.id = intake_queue.client_id "
        "LEFT JOIN intake_supplies ON intake_supplies.id = intake_queue.supply_id "
        "WHERE intake_queue.state IN (%s) ORDER BY intake_queue.id" % marks,
        tuple(states),
    ).fetchall()
    conn.close()
    return rows


def get_intake_row(row_id):
    conn = connect()
    row = conn.execute(
        "SELECT intake_queue.*, clients.name AS client_name FROM intake_queue "
        "JOIN clients ON clients.id = intake_queue.client_id WHERE intake_queue.id = ?",
        (row_id,),
    ).fetchone()
    conn.close()
    return row


def queued_barcodes(client_id):
    conn = connect()
    rows = conn.execute(
        "SELECT barcode FROM intake_queue WHERE client_id = ? AND state IN ('draft', 'warn')",
        (client_id,),
    ).fetchall()
    conn.close()
    return {barcode_norm(r["barcode"]) for r in rows if r["barcode"]}


def insert_invoice(client_id, ms_invoice_id, ms_number, storage, intake, ship, total, lots_count, created_at, author, period_from="", period_to=""):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO invoices (client_id, ms_invoice_id, ms_number, storage, intake, ship, total, "
        "lots_count, period_from, period_to, created_at, author) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id, ms_invoice_id, ms_number, storage, intake, ship, total,
            lots_count, period_from or "", period_to or "", created_at, author,
        ),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def add_invoice_lot(invoice_id, lot_id, storage, days=0, liter_days=0, period_from="", period_to="", ship=0):
    conn = connect()
    conn.execute(
        "INSERT INTO invoice_lots (invoice_id, lot_id, storage, intake, ship, days, liter_days, "
        "period_from, period_to) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)",
        (invoice_id, lot_id, storage, ship, days, liter_days, period_from or "", period_to or ""),
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


def get_invoice(invoice_id):
    conn = connect()
    row = conn.execute(
        "SELECT invoices.*, clients.name AS client_name FROM invoices "
        "JOIN clients ON clients.id = invoices.client_id WHERE invoices.id = ?",
        (int(invoice_id),),
    ).fetchone()
    conn.close()
    return row


def list_invoice_positions(invoice_id):
    conn = connect()
    rows = conn.execute(
        "SELECT invoice_lots.storage, invoice_lots.ship, invoice_lots.days, invoice_lots.liter_days, "
        "invoice_lots.period_from, invoice_lots.period_to, "
        "lots.article, lots.barcode, lots.gtin, lots.name, lots.liters, lots.pick_rate, "
        "lots.qty_in, lots.received_at, lots.accepted_at "
        "FROM invoice_lots JOIN lots ON lots.id = invoice_lots.lot_id "
        "WHERE invoice_lots.invoice_id = ? ORDER BY invoice_lots.id",
        (int(invoice_id),),
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


# Поля отправления, которые приходят с площадки. work_state сюда не входит:
# это наша локальная отметка оператора, и выгрузка её не трогает.
SHIP_EXTRA = (
    "status_group", "accepted_at", "deadline_at", "track", "warehouse", "image",
    # куда везти задание и его габаритный тип
    "office", "cargo_type", "pickup_allowed",
)


def upsert_shipment(client_id, cabinet_id, marketplace, kind, ext_id, status, shipped_at, article, barcode, name, qty, ms_order_id, marks_count, pulled_at, extra=None):
    extra = extra or {}
    conn = connect()
    existing = conn.execute(
        "SELECT id, pickup_allowed, office FROM shipments WHERE cabinet_id = ? AND kind = ? AND ext_id = ?",
        (cabinet_id, kind, ext_id),
    ).fetchone()
    # адрес сдачи и флаг ПВЗ приходят не из выгрузки, а от поставки: точку
    # выбирают в ЛК. Выгрузка их не знает и затирать не должна
    for col in ("pickup_allowed", "office"):
        if existing and not extra.get(col):
            extra[col] = str(col in existing.keys() and existing[col] or "")
    more = [str(extra.get(col) or "") for col in SHIP_EXTRA]
    if existing:
        conn.execute(
            "UPDATE shipments SET client_id=?, marketplace=?, status=?, shipped_at=?, article=?, "
            "barcode=?, name=?, qty=?, ms_order_id=?, marks_count=?, pulled_at=?, "
            + ", ".join("%s=?" % col for col in SHIP_EXTRA)
            + " WHERE id=?",
            [
                client_id, marketplace, status, shipped_at, article, barcode, name, qty,
                ms_order_id, marks_count, pulled_at,
            ] + more + [existing["id"]],
        )
        sid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO shipments (client_id, cabinet_id, marketplace, kind, ext_id, status, shipped_at, "
            "article, barcode, name, qty, ms_order_id, marks_count, pulled_at, "
            + ", ".join(SHIP_EXTRA)
            + ") VALUES (%s)" % ", ".join("?" * (14 + len(SHIP_EXTRA))),
            [
                client_id, cabinet_id, marketplace, kind, ext_id, status, shipped_at,
                article, barcode, name, qty, ms_order_id, marks_count, pulled_at,
            ] + more,
        )
        sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_shipments_by_ext(cabinet_id, kind, ext_ids):
    """Отправления кабинета по номерам площадки."""
    ids = [str(x) for x in ext_ids if x]
    if not ids:
        return []
    conn = connect()
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT * FROM shipments WHERE cabinet_id = ? AND kind = ? AND ext_id IN (%s)" % q,
        [int(cabinet_id), kind] + ids,
    ).fetchall()
    conn.close()
    return rows


def set_shipment_platform(shipment_id, status, status_group, work_state=None):
    """Статус с площадки. work_state трогаем, только если передали явно."""
    conn = connect()
    if work_state is None:
        conn.execute(
            "UPDATE shipments SET status = ?, status_group = ? WHERE id = ?",
            (status or "", status_group or "", int(shipment_id)),
        )
    else:
        conn.execute(
            "UPDATE shipments SET status = ?, status_group = ?, work_state = ? WHERE id = ?",
            (status or "", status_group or "", work_state or "", int(shipment_id)),
        )
    conn.commit()
    conn.close()


def set_work_state(ids, work_state):
    """Складская отметка: на сборке, собрано, отгружено. Площадку не трогает."""
    if not ids:
        return 0
    conn = connect()
    q = ",".join("?" * len(ids))
    cur = conn.execute(
        "UPDATE shipments SET work_state = ? WHERE id IN (%s)" % q,
        [work_state or ""] + [int(x) for x in ids],
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def map_shipments(cabinet_id, kind=""):
    """Что по кабинету уже лежит у нас: номер отправления → строка.

    Нужна выгрузке Ozon, чтобы решить, за кем идти отдельным запросом.
    """
    conn = connect()
    args = [int(cabinet_id)]
    sql = "SELECT * FROM shipments WHERE cabinet_id = ?"
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return {str(r["ext_id"]): r for r in rows}


def list_open_shipments(cabinet_id, kind="", groups=("ready", "shipped")):
    """Отправления кабинета, которые ждут отгрузки или уже уехали.

    Нужны ночной сверке: у этих статус на площадке живёт дальше, а у нас может
    остаться складская отметка «ожидает отгрузки» навсегда.
    """
    if not groups:
        return []
    conn = connect()
    args = [int(cabinet_id)]
    sql = "SELECT * FROM shipments WHERE cabinet_id = ?"
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " AND %s IN (%s)" % (EFF_GROUP, ",".join("?" * len(groups)))
    args.extend(groups)
    rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    conn.close()
    return rows


def set_shipment_status(ship_id, status, status_group, clear_work=False, track=None):
    """Обновить статус площадки. `clear_work` снимает складскую отметку.

    Отметку снимаем, когда площадка ушла вперёд: иначе work_state держит заказ на
    вкладке «Ожидают отгрузки», хотя он уже доставлен.
    """
    sets = ["status = ?", "status_group = ?"]
    vals = [status or "", status_group or ""]
    if clear_work:
        sets.append("work_state = ''")
    if track is not None:
        sets.append("track = ?")
        vals.append(track)
    vals.append(int(ship_id))
    conn = connect()
    cur = conn.execute("UPDATE shipments SET %s WHERE id = ?" % ", ".join(sets), vals)
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def set_shipment_supply(ids, supply_ext, trbx_ext=None):
    """Пометить отправления поставкой WB и, если задано, грузоместом.

    trbx_ext=None оставляет грузоместо как было: заказ сначала кладут в поставку,
    а по коробкам раскладывают отдельным движением.
    """
    if not ids:
        return 0
    conn = connect()
    q = ",".join("?" * len(ids))
    sql = "UPDATE shipments SET supply_ext = ?"
    args = [supply_ext or ""]
    if trbx_ext is not None:
        sql += ", trbx_ext = ?"
        args.append(trbx_ext or "")
    cur = conn.execute(sql + " WHERE id IN (%s)" % q, args + [int(x) for x in ids])
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def delete_shipments_by_ext(cabinet_id, kind, ext_ids):
    """Убрать отправления, которых на площадке больше нет.

    Ozon при сборке с дроблением заменяет номер отправления на несколько новых:
    старый номер перестаёт существовать, и держать его в списке — врать сборщику.
    """
    if not ext_ids:
        return 0
    conn = connect()
    q = ",".join("?" * len(ext_ids))
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM shipments WHERE cabinet_id = ? AND kind = ? AND ext_id IN (%s)" % q,
            [int(cabinet_id), kind] + [str(x) for x in ext_ids],
        )
    ]
    if ids:
        idq = ",".join("?" * len(ids))
        conn.execute("DELETE FROM shipment_marks WHERE shipment_id IN (%s)" % idq, ids)
        conn.execute("DELETE FROM shipments WHERE id IN (%s)" % idq, ids)
    conn.commit()
    conn.close()
    return len(ids)


def insert_wb_supply(
    client_id,
    cabinet_id,
    ext_id,
    name,
    created_at,
    author,
    cargo_type="",
    pickup_allowed="",
    shipping_point="",
):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO wb_supplies (client_id, cabinet_id, ext_id, name, created_at, author, "
        "cargo_type, pickup_allowed, shipping_point) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
            cabinet_id,
            ext_id,
            name or "",
            created_at,
            author or "",
            str(cargo_type or ""),
            str(pickup_allowed or ""),
            str(shipping_point or ""),
        ),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def set_wb_supply_dropoff(supply_id, cargo_type, pickup_allowed, shipping_point=""):
    """Габарит, флаг ПВЗ и выбранная точка: от точки зависит адрес сдачи."""
    conn = connect()
    conn.execute(
        "UPDATE wb_supplies SET cargo_type = ?, pickup_allowed = ?, shipping_point = ? WHERE id = ?",
        (
            str(cargo_type or ""),
            str(pickup_allowed or ""),
            str(shipping_point or ""),
            int(supply_id),
        ),
    )
    conn.commit()
    conn.close()


def set_wb_supply_point(supply_id, shipping_point, shipping_dt=""):
    """Выбранная точка сдачи и дата отгрузки. Пишем только после ответа площадки."""
    conn = connect()
    conn.execute(
        "UPDATE wb_supplies SET shipping_point = ?, shipping_dt = ? WHERE id = ?",
        (str(shipping_point or ""), str(shipping_dt or ""), int(supply_id)),
    )
    conn.commit()
    conn.close()


def save_wb_points(rows, when=""):
    """Сложить справочник пунктов отгрузки WB. Возвращает, сколько записей легло."""
    items = []
    for row in rows or []:
        try:
            pid = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        items.append(
            (
                pid,
                str(row.get("name") or ""),
                str(row.get("address") or ""),
                str(row.get("city") or ""),
                str(row.get("officeType") or ""),
                ",".join(str(x) for x in (row.get("cargoTypes") or [])),
                1 if row.get("fulfillment") else 0,
                when or "",
            )
        )
    if not items:
        return 0
    conn = connect()
    conn.executemany(
        "INSERT INTO wb_points (id, name, address, city, office_type, cargo_types, fulfillment, pulled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, address = excluded.address, "
        "city = excluded.city, office_type = excluded.office_type, "
        "cargo_types = excluded.cargo_types, fulfillment = excluded.fulfillment, "
        "pulled_at = excluded.pulled_at",
        items,
    )
    conn.commit()
    conn.close()
    return len(items)


def get_wb_point(point_id):
    try:
        pid = int(point_id or 0)
    except (TypeError, ValueError):
        return None
    if not pid:
        return None
    conn = connect()
    row = conn.execute("SELECT * FROM wb_points WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return row


def list_wb_points(city="", office_type="", cargo_type="", query="", limit=300):
    """Точки из справочника: город, тип точки, габарит, поиск по адресу."""
    where = []
    args = []
    if city:
        where.append("city LIKE ?")
        args.append("%" + str(city) + "%")
    if office_type:
        where.append("office_type = ?")
        args.append(str(office_type))
    if cargo_type:
        where.append("(',' || cargo_types || ',') LIKE ?")
        args.append("%," + str(cargo_type) + ",%")
    if query:
        where.append("(address LIKE ? OR name LIKE ? OR CAST(id AS TEXT) LIKE ?)")
        args.extend(["%" + str(query) + "%"] * 3)
    sql = "SELECT * FROM wb_points"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY address LIMIT ?"
    args.append(int(limit or 300))
    conn = connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def wb_points_count():
    conn = connect()
    row = conn.execute("SELECT COUNT(*) AS n FROM wb_points").fetchone()
    conn.close()
    return int(row["n"] if row else 0)


def set_supply_shipments_dropoff(cabinet_id, supply_ext, office, pickup_allowed):
    """Тот же адрес сдачи на все задания поставки, чтобы колонка не врала."""
    conn = connect()
    conn.execute(
        "UPDATE shipments SET office = ?, pickup_allowed = ? "
        "WHERE cabinet_id = ? AND supply_ext = ?",
        (office or "", str(pickup_allowed or ""), int(cabinet_id), supply_ext),
    )
    conn.commit()
    conn.close()


def get_wb_supply(supply_id):
    conn = connect()
    row = conn.execute(
        "SELECT wb_supplies.*, clients.name AS client_name FROM wb_supplies "
        "JOIN clients ON clients.id = wb_supplies.client_id WHERE wb_supplies.id = ?",
        (int(supply_id),),
    ).fetchone()
    conn.close()
    return row


def set_wb_supply_state(supply_id, state, when=None):
    """open — сборка, ready — на отгрузке, delivered — сдана."""
    conn = connect()
    if when is None:
        conn.execute(
            "UPDATE wb_supplies SET state = ? WHERE id = ?",
            (state, int(supply_id)),
        )
    else:
        conn.execute(
            "UPDATE wb_supplies SET state = ?, delivered_at = ? WHERE id = ?",
            (state, when, int(supply_id)),
        )
    conn.commit()
    conn.close()


def mark_wb_supply_delivered(supply_id, when):
    set_wb_supply_state(supply_id, "delivered", when)


def list_wb_supplies(client_id=None, state="", limit=100):
    conn = connect()
    sql = (
        "SELECT wb_supplies.*, clients.name AS client_name, %s "
        "FROM wb_supplies JOIN clients ON clients.id = wb_supplies.client_id WHERE 1=1"
        % WB_SUPPLY_COUNTS
    )
    args = []
    if client_id:
        sql += " AND wb_supplies.client_id = ?"
        args.append(int(client_id))
    if state:
        sql += " AND wb_supplies.state = ?"
        args.append(state)
    sql += " ORDER BY wb_supplies.id DESC LIMIT ?"
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


WB_SUPPLY_COUNTS = (
    "(SELECT COUNT(*) FROM wb_boxes b WHERE b.supply_id = wb_supplies.id) AS boxes, "
    "(SELECT COUNT(*) FROM shipments s WHERE s.supply_ext = wb_supplies.ext_id "
    " AND s.cabinet_id = wb_supplies.cabinet_id) AS orders, "
    "(SELECT COUNT(*) FROM shipments s WHERE s.supply_ext = wb_supplies.ext_id "
    " AND s.cabinet_id = wb_supplies.cabinet_id AND COALESCE(s.trbx_ext,'') = '') AS loose"
)


def find_wb_supplies(ext_ids):
    """Поставки по номерам площадки: нужны таблице «Заказов» строкой-родителем."""
    ids = [str(x) for x in ext_ids if x]
    if not ids:
        return []
    conn = connect()
    rows = conn.execute(
        "SELECT wb_supplies.*, clients.name AS client_name, %s "
        "FROM wb_supplies JOIN clients ON clients.id = wb_supplies.client_id "
        "WHERE wb_supplies.ext_id IN (%s) ORDER BY wb_supplies.id DESC"
        % (WB_SUPPLY_COUNTS, ",".join("?" * len(ids))),
        ids,
    ).fetchall()
    conn.close()
    return rows


def insert_wb_boxes(supply_id, ext_ids, created_at):
    conn = connect()
    conn.executemany(
        "INSERT OR IGNORE INTO wb_boxes (supply_id, ext_id, created_at) VALUES (?, ?, ?)",
        [(int(supply_id), str(x), created_at) for x in ext_ids],
    )
    conn.commit()
    conn.close()


def list_wb_boxes(supply_id):
    conn = connect()
    rows = conn.execute(
        "SELECT wb_boxes.*, "
        "(SELECT COUNT(*) FROM shipments s WHERE s.trbx_ext = wb_boxes.ext_id) AS orders "
        "FROM wb_boxes WHERE supply_id = ? ORDER BY wb_boxes.id",
        (int(supply_id),),
    ).fetchall()
    conn.close()
    return rows


def get_wb_box(box_id):
    conn = connect()
    row = conn.execute("SELECT * FROM wb_boxes WHERE id = ?", (int(box_id),)).fetchone()
    conn.close()
    return row


def delete_wb_boxes(supply_id, ext_ids):
    if not ext_ids:
        return 0
    conn = connect()
    q = ",".join("?" * len(ext_ids))
    conn.execute(
        "UPDATE shipments SET trbx_ext = '' WHERE trbx_ext IN (%s)" % q,
        [str(x) for x in ext_ids],
    )
    cur = conn.execute(
        "DELETE FROM wb_boxes WHERE supply_id = ? AND ext_id IN (%s)" % q,
        [int(supply_id)] + [str(x) for x in ext_ids],
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def list_supply_shipments(cabinet_id, supply_ext):
    conn = connect()
    rows = conn.execute(
        "SELECT shipments.*, clients.name AS client_name FROM shipments "
        "JOIN clients ON clients.id = shipments.client_id "
        "WHERE shipments.cabinet_id = ? AND shipments.supply_ext = ? "
        "ORDER BY COALESCE(NULLIF(shipments.trbx_ext,''), 'яя'), shipments.id",
        (int(cabinet_id), str(supply_ext)),
    ).fetchall()
    conn.close()
    return rows


def replace_shipment_marks(shipment_id, rows):
    conn = connect()
    conn.execute("DELETE FROM shipment_marks WHERE shipment_id = ?", (shipment_id,))
    conn.executemany(
        "INSERT INTO shipment_marks (shipment_id, code, gtin, article) VALUES (?, ?, ?, ?)",
        [(shipment_id, row.get("code") or "", row.get("gtin") or "", row.get("article") or "") for row in rows],
    )
    # счётчик в таблице «Сборки» считаем здесь же: иначе он разъедется с кодами,
    # когда их вносит склад сканером, а не выгрузка с площадки
    conn.execute(
        "UPDATE shipments SET marks_count = "
        "(SELECT count(*) FROM shipment_marks WHERE shipment_id = ?) WHERE id = ?",
        (shipment_id, shipment_id),
    )
    conn.commit()
    conn.close()


STATUS_RU = {
    "awaiting_packaging": "Ожидает упаковки",
    "awaiting_deliver": "Ожидает отгрузки",
    "awaiting_registration": "Ожидает регистрации",
    "acceptance_in_progress": "Приёмка",
    "awaiting_approve": "Ожидает подтверждения",
    "awaiting_verification": "На проверке",
    "delivering": "В доставке",
    "delivered": "Доставлен",
    "cancelled": "Отменён",
    "canceled": "Отменён",
    "canceled_by_client": "Отменён клиентом",
    "declined_by_client": "Отклонён клиентом",
    "declined": "Отклонён",
    "not_accepted": "Не принят",
    "arbitration": "Спор",
    "client_arbitration": "Спор клиента",
    "sent_by_seller": "Отправлен продавцом",
    "new": "Новый",
    "confirm": "Подтверждён",
    "complete": "Собран",
    "waiting": "Ожидает",
    "sorted": "На сортировке",
    "sold": "Продан",
    "ready_for_pickup": "Готов к выдаче",
    "defect": "Брак",
    "cancel": "Отменён",
    "rejected": "Отклонён",
    "fbs": "FBS",
    "fbo": "FBO",
}


def status_label(raw):
    text = str(raw or "").strip()
    if not text:
        return "—"
    if " / " in text:
        seen = []
        for part in text.split("/"):
            label = status_label(part.strip())
            if label not in seen:
                seen.append(label)
        return " · ".join(seen)
    low = text.lower()
    if low in STATUS_RU:
        return STATUS_RU[low]
    return text.replace("_", " ")


def shipped_by_day(client_id, day_from, day_to):
    """Отгруженное по артикулам и дням: основа недельного отчёта клиенту.

    Считаем по отправлениям, а не по календарю хранения: тот сводит по партиям
    FIFO, и в отчёте клиента появились бы чужие даты. Берём только то, что уже
    уехало: отгружено или доставлено.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT substr(shipments.shipped_at,1,10) AS day, shipments.article AS article, "
        "shipments.barcode AS barcode, shipments.name AS name, shipments.marketplace AS mp, "
        "SUM(shipments.qty) AS qty FROM shipments "
        "WHERE shipments.client_id = ? AND %s IN ('shipped','delivered') "
        "AND substr(shipments.shipped_at,1,10) >= ? AND substr(shipments.shipped_at,1,10) <= ? "
        "GROUP BY day, shipments.article, shipments.barcode "
        "ORDER BY shipments.article, day" % EFF_GROUP,
        (int(client_id), day_from, day_to),
    ).fetchall()
    conn.close()
    return rows


def list_shipments(client_id=None, marketplace="", kind="", marked=None, day_from="", day_to="", query=""):
    conn = connect()
    sql = (
        "SELECT shipments.*, clients.name AS client_name FROM shipments "
        "JOIN clients ON clients.id = shipments.client_id WHERE 1=1"
    )
    args = []
    # отменённые храним ради вкладки «Отменены» в Сборке, но в отгрузки и выгрузку
    # кодов маркировки они попадать не должны
    sql += " AND COALESCE(shipments.status_group, '') <> 'cancelled'"
    if client_id:
        sql += " AND shipments.client_id = ?"
        args.append(int(client_id))
    if marketplace:
        sql += " AND shipments.marketplace = ?"
        args.append(marketplace)
    if kind:
        sql += " AND shipments.kind = ?"
        args.append(kind)
    if marked is True:
        sql += " AND shipments.marks_count > 0"
    if marked is False:
        sql += " AND shipments.marks_count = 0"
    if day_from:
        sql += " AND substr(shipments.shipped_at,1,10) >= ?"
        args.append(day_from)
    if day_to:
        sql += " AND substr(shipments.shipped_at,1,10) <= ?"
        args.append(day_to)
    text = (query or "").strip().lower()
    rows = conn.execute(sql + " ORDER BY shipments.shipped_at DESC, shipments.id DESC", args).fetchall()
    conn.close()
    if not text:
        return rows
    out = []
    for row in rows:
        hay = " ".join(
            str(row[k] or "").lower()
            for k in ("ext_id", "article", "barcode", "name", "status", "client_name")
        )
        hay += " " + status_label(row["status"]).lower()
        if text in hay:
            out.append(row)
    return out


# Группа, которую видит оператор. work_state — наша складская отметка
# (новые → на сборке → ожидают отгрузки → отгружены). Считаем на чтении,
# чтобы выгрузка с площадки не стирала ход сборщика. Отмена и «получено
# покупателем» важнее нашей отметки. «В доставке» у WB (shipped) — нет:
# после «На отгрузку» задания должны остаться во вкладке «Ожидают отгрузки»,
# пока склад сам не нажмёт «Отгружено».
EFF_GROUP = (
    "CASE"
    " WHEN COALESCE(shipments.status_group,'') IN ('cancelled','delivered')"
    " THEN shipments.status_group"
    " WHEN COALESCE(shipments.work_state,'') IN ('assembling','ready','shipped')"
    " THEN shipments.work_state"
    " ELSE COALESCE(shipments.status_group,'') END"
)

# Открытая работа склада. Смена «принят» её не режет: иначе при «все
# контрагенты» сегодняшние заказы одного клиента держат период, а вчерашняя
# поставка другого пропадает из вкладки. Закрытые статусы период оставляют.
OPEN_GROUPS = ("new", "assembling", "ready")
ASM_WHEN = "replace(COALESCE(NULLIF(shipments.accepted_at, ''), shipments.shipped_at), 'T', ' ')"


def _assembly_date_clauses(since, until, keep_floor, group=""):
    """Дата для выборки сборки: открытая работа — пол 14 дней, закрытая — смена."""
    clauses = []
    args = []
    floor = ship_keep_since() if keep_floor else ""
    period = []
    pargs = []
    if since:
        period.append("%s >= ?" % ASM_WHEN)
        pargs.append(since)
    if until:
        period.append("%s <= ?" % ASM_WHEN)
        pargs.append(until)
    open_in = "%s IN ('new','assembling','ready')" % EFF_GROUP
    closed = "%s NOT IN ('new','assembling','ready')" % EFF_GROUP

    if group in OPEN_GROUPS:
        if floor:
            clauses.append("%s >= ?" % ASM_WHEN)
            args.append(floor)
        return clauses, args

    if group:
        if floor:
            clauses.append("%s >= ?" % ASM_WHEN)
            args.append(floor)
        clauses.extend(period)
        args.extend(pargs)
        return clauses, args

    if not period:
        if floor:
            clauses.append("%s >= ?" % ASM_WHEN)
            args.append(floor)
        return clauses, args

    open_sql = open_in
    open_args = []
    if floor:
        open_sql = "(%s AND %s >= ?)" % (open_in, ASM_WHEN)
        open_args.append(floor)
    closed_sql = closed
    closed_args = []
    if floor:
        closed_sql = "(%s AND %s >= ?)" % (closed, ASM_WHEN)
        closed_args.append(floor)
    closed_sql = "(%s AND %s)" % (closed_sql, " AND ".join(period))
    closed_args.extend(pargs)
    clauses.append("(%s OR %s)" % (open_sql, closed_sql))
    args.extend(open_args + closed_args)
    return clauses, args


def _assembly_filters(
    client_id=None, group="", marketplace="", kind="", article="", query="", since="", until="", keep_floor=True,
):
    sql = " JOIN clients ON clients.id = shipments.client_id WHERE 1=1"
    args = []
    if client_id:
        sql += " AND shipments.client_id = ?"
        args.append(int(client_id))
    if marketplace:
        sql += " AND shipments.marketplace = ?"
        args.append(marketplace)
    if kind:
        sql += " AND shipments.kind = ?"
        args.append(kind.lower())
    if group:
        sql += " AND %s = ?" % EFF_GROUP
        args.append(group)
    date_sql, date_args = _assembly_date_clauses(since, until, keep_floor, group)
    for clause in date_sql:
        sql += " AND " + clause
    args.extend(date_args)
    # Артикул сверяем целиком, а не по вхождению: по этому фильтру оператор
    # отбирает позиции на печать этикеток, и «777» не должен тянуть «7777».
    art = (article or "").strip().lower()
    if art:
        sql += " AND lower(trim(ifnull(shipments.article,''))) = ?"
        args.append(art)
    text = (query or "").strip().lower()
    if text:
        sql += (
            " AND lower(ifnull(shipments.ext_id,'') || ' ' || ifnull(shipments.article,'') || ' ' || "
            "ifnull(shipments.barcode,'') || ' ' || ifnull(shipments.name,'') || ' ' || "
            "ifnull(shipments.status,'') || ' ' || ifnull(clients.name,'') || ' ' || "
            "ifnull(shipments.track,'') || ' ' || ifnull(shipments.warehouse,'')) LIKE ?"
        )
        args.append("%" + text + "%")
    return sql, args


def list_assembly(client_id=None, group="", marketplace="", kind="", article="", query="", since="", until="", keep_floor=True, limit=0):
    """Отправления для раздела «Сборка».

    Период режем по «принят» с точностью до минуты, но только на закрытых
    вкладках. Открытая работа (новые, сборка, ожидают отгрузки) период смены
    не режет — иначе «все контрагенты» прячет чужую вчерашнюю поставку.
    Формат since / until — 'ГГГГ-ММ-ДД ЧЧ:ММ'.
    """
    where, args = _assembly_filters(
        client_id=client_id, group=group, marketplace=marketplace, kind=kind,
        article=article, query=query, since=since, until=until, keep_floor=keep_floor,
    )
    sql = (
        "SELECT shipments.*, clients.name AS client_name, %s AS eff_group FROM shipments"
        "%s ORDER BY %s DESC, shipments.id DESC" % (EFF_GROUP, where, ASM_WHEN)
    )
    if limit:
        sql += " LIMIT %d" % int(limit)
    conn = connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def list_assembly_supply_exts(
    client_id=None, group="", marketplace="", kind="", article="", query="", since="", until="", keep_floor=True,
):
    """Номера поставок вкладки без лимита строк: иначе поставка за сотой строкой пропадает."""
    where, args = _assembly_filters(
        client_id=client_id, group=group, marketplace=marketplace, kind=kind,
        article=article, query=query, since=since, until=until, keep_floor=keep_floor,
    )
    conn = connect()
    rows = conn.execute(
        "SELECT DISTINCT shipments.supply_ext AS ext FROM shipments"
        "%s AND COALESCE(shipments.supply_ext,'') <> ''" % where,
        args,
    ).fetchall()
    conn.close()
    return [r["ext"] for r in rows]


def assembly_counts(client_id=None, marketplace="", kind="", article="", query="", since="", until="", keep_floor=True):
    """Счётчики на вкладках. Считаем в SQL: раньше тянули все строки в память."""
    where, args = _assembly_filters(
        client_id=client_id, marketplace=marketplace, kind=kind,
        article=article, query=query, since=since, until=until, keep_floor=keep_floor,
    )
    conn = connect()
    rows = conn.execute(
        "SELECT %s AS g, COUNT(*) AS n FROM shipments%s GROUP BY g" % (EFF_GROUP, where),
        args,
    ).fetchall()
    conn.close()
    counts = {(r["g"] or ""): r["n"] for r in rows}
    return counts, sum(counts.values())


def get_shipments_by_ids(ids):
    if not ids:
        return []
    conn = connect()
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT shipments.*, clients.name AS client_name FROM shipments "
        "JOIN clients ON clients.id = shipments.client_id "
        "WHERE shipments.id IN (%s) ORDER BY shipments.shipped_at DESC, shipments.id DESC" % q,
        [int(x) for x in ids],
    ).fetchall()
    conn.close()
    return rows


def list_shipment_marks(ids):
    if not ids:
        return []
    conn = connect()
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT shipment_marks.*, shipments.ext_id, shipments.marketplace, shipments.kind, "
        "shipments.shipped_at, shipments.status, shipments.barcode AS ship_barcode, "
        "shipments.name AS ship_name, shipments.article AS ship_article, "
        "shipments.client_id AS client_id, clients.name AS client_name "
        "FROM shipment_marks JOIN shipments ON shipments.id = shipment_marks.shipment_id "
        "JOIN clients ON clients.id = shipments.client_id "
        "WHERE shipment_marks.shipment_id IN (%s) ORDER BY shipments.shipped_at, shipment_marks.id" % q,
        [int(x) for x in ids],
    ).fetchall()
    conn.close()
    return rows


def _day_filter(alias, day_from, day_to, args):
    sql = ""
    if day_from:
        sql += " AND substr(%s,1,10) >= ?" % alias
        args.append(day_from)
    if day_to:
        sql += " AND substr(%s,1,10) <= ?" % alias
        args.append(day_to)
    return sql


def overview_stats(client_id=None, day_from="", day_to=""):
    conn = connect()
    args = []
    where = "1=1"
    if client_id:
        where += " AND client_id = ?"
        args.append(int(client_id))
    ship_where = where + _day_filter("shipped_at", day_from, day_to, args)
    tot = conn.execute(
        "SELECT count(*) n, coalesce(sum(qty),0) qty, coalesce(sum(marks_count),0) marks, "
        "sum(CASE WHEN marks_count = 0 THEN 1 ELSE 0 END) without "
        "FROM shipments WHERE " + ship_where,
        args,
    ).fetchone()
    by_mp = [
        dict(r)
        for r in conn.execute(
            "SELECT marketplace, count(*) n, coalesce(sum(marks_count),0) marks "
            "FROM shipments WHERE " + ship_where + " GROUP BY marketplace",
            args,
        ).fetchall()
    ]
    by_kind = [
        dict(r)
        for r in conn.execute(
            "SELECT kind, count(*) n, coalesce(sum(marks_count),0) marks "
            "FROM shipments WHERE " + ship_where + " GROUP BY kind",
            args,
        ).fetchall()
    ]
    days = [
        dict(r)
        for r in conn.execute(
            "SELECT substr(shipped_at,1,10) day, count(*) n, coalesce(sum(marks_count),0) marks "
            "FROM shipments WHERE " + ship_where + " GROUP BY 1 ORDER BY 1",
            args,
        ).fetchall()
    ]
    inv_args = []
    inv_where = "1=1"
    if client_id:
        inv_where += " AND client_id = ?"
        inv_args.append(int(client_id))
    inv_where += _day_filter("created_at", day_from, day_to, inv_args)
    inv = conn.execute(
        "SELECT count(*) n, coalesce(sum(total),0) total FROM invoices WHERE " + inv_where,
        inv_args,
    ).fetchone()
    intake_args = []
    intake_sql = "SELECT count(*) FROM intake_queue WHERE state IN ('draft','warn')"
    if client_id:
        intake_sql += " AND client_id = ?"
        intake_args.append(int(client_id))
    intake_n = conn.execute(intake_sql, intake_args).fetchone()[0]
    last = conn.execute(
        "SELECT max(last_ok_at) FROM cabinets WHERE active = 1 AND ifnull(token,'') != ''"
    ).fetchone()[0]
    clients_n = conn.execute("SELECT count(*) FROM clients WHERE active = 1").fetchone()[0]
    conn.close()
    return {
        "ships": {
            "n": int(tot["n"] or 0),
            "qty": float(tot["qty"] or 0),
            "marks": int(tot["marks"] or 0),
            "without": int(tot["without"] or 0),
        },
        "by_mp": by_mp,
        "by_kind": by_kind,
        "days": days,
        "invoices": {"n": int(inv["n"] or 0), "total": float(inv["total"] or 0)},
        "intake": int(intake_n or 0),
        "last_ok": last or "",
        "clients": int(clients_n or 0),
    }
