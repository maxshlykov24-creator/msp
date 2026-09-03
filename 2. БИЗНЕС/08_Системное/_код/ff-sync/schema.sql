CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ms_counterparty_id TEXT,
    ms_org_id TEXT,
    ms_store_id TEXT,
    tariff_storage REAL,
    tariff_intake REAL,
    tariff_ship REAL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cabinets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    name TEXT,
    token TEXT,
    client_id_ext TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    last_ok_at TEXT,
    last_error TEXT,
    last_pull_at TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS sku_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    cabinet_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    ext_key TEXT NOT NULL,
    ext_article TEXT,
    ext_barcode TEXT,
    ms_product_id TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (cabinet_id) REFERENCES cabinets(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS sku_map_cabinet_ext
    ON sku_map (cabinet_id, ext_key);

CREATE TABLE IF NOT EXISTS orders_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cabinet_id INTEGER NOT NULL,
    ext_order_id TEXT NOT NULL,
    ms_order_id TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT,
    FOREIGN KEY (cabinet_id) REFERENCES cabinets(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS orders_log_cabinet_ext
    ON orders_log (cabinet_id, ext_order_id);

CREATE TABLE IF NOT EXISTS storage_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    client_id INTEGER NOT NULL,
    ms_product_id TEXT,
    qty REAL,
    liters REAL,
    stock_days REAL,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS catalog_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    cabinet_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    ext_key TEXT NOT NULL,
    ext_article TEXT,
    ext_barcode TEXT,
    barcode_norm TEXT,
    name TEXT,
    size TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (cabinet_id) REFERENCES cabinets(id)
);

CREATE INDEX IF NOT EXISTS catalog_cache_client_bc
    ON catalog_cache (client_id, barcode_norm);
CREATE INDEX IF NOT EXISTS catalog_cache_client_art
    ON catalog_cache (client_id, ext_article);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    ms_product_id TEXT NOT NULL,
    article TEXT,
    barcode TEXT,
    gtin TEXT,
    name TEXT,
    tracking_type TEXT,
    liters REAL,
    qty_in REAL NOT NULL,
    qty_left REAL NOT NULL,
    received_at TEXT NOT NULL,
    ms_supply_id TEXT,
    billed_days REAL NOT NULL DEFAULT 0,
    billed_in REAL NOT NULL DEFAULT 0,
    billed_out REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE INDEX IF NOT EXISTS lots_fifo
    ON lots (client_id, ms_product_id, received_at, id);

CREATE TABLE IF NOT EXISTS lot_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL,
    qty REAL NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (lot_id) REFERENCES lots(id)
);

CREATE TABLE IF NOT EXISTS intake_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    barcode TEXT,
    article TEXT,
    name TEXT,
    marketplace TEXT,
    gtin TEXT,
    tracking_type TEXT,
    liters REAL,
    qty REAL NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'draft',
    note TEXT,
    ms_product_id TEXT,
    ms_order_name TEXT,
    created_at TEXT NOT NULL,
    author TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE INDEX IF NOT EXISTS intake_queue_state ON intake_queue (state, id);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    ms_invoice_id TEXT,
    ms_number TEXT,
    storage REAL NOT NULL DEFAULT 0,
    intake REAL NOT NULL DEFAULT 0,
    ship REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    lots_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    author TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS invoice_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    lot_id INTEGER NOT NULL,
    storage REAL NOT NULL DEFAULT 0,
    intake REAL NOT NULL DEFAULT 0,
    ship REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    FOREIGN KEY (lot_id) REFERENCES lots(id)
);
