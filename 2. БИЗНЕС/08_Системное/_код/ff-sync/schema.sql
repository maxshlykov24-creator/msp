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
    tariff_pick REAL,
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
    gtin TEXT,
    tracking_type TEXT,
    subject TEXT,
    need_kiz INTEGER,
    image TEXT,
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
    dims TEXT,
    tariff REAL,
    pick_rate REAL,
    qty_in REAL NOT NULL,
    qty_left REAL NOT NULL,
    received_at TEXT NOT NULL,
    accepted_at TEXT,
    ms_supply_id TEXT,
    billed_days REAL NOT NULL DEFAULT 0,
    billed_until TEXT,
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

CREATE TABLE IF NOT EXISTS intake_supplies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    number TEXT,
    client_name TEXT,
    contract TEXT,
    planned_at TEXT,
    contact TEXT,
    carrier TEXT,
    car_plate TEXT,
    places TEXT,
    marking TEXT,
    terms TEXT,
    source_name TEXT,
    rows_total INTEGER NOT NULL DEFAULT 0,
    qty_total REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    author TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS intake_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    supply_id INTEGER,
    barcode TEXT,
    article TEXT,
    name TEXT,
    marketplace TEXT,
    gtin TEXT,
    tracking_type TEXT,
    liters REAL,
    dims TEXT,
    tariff REAL,
    pick_rate REAL,
    qty REAL NOT NULL DEFAULT 0,
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
    period_from TEXT,
    period_to TEXT,
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
    days REAL NOT NULL DEFAULT 0,
    liter_days REAL NOT NULL DEFAULT 0,
    period_from TEXT,
    period_to TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    FOREIGN KEY (lot_id) REFERENCES lots(id)
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    cabinet_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    kind TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    status TEXT,
    shipped_at TEXT,
    article TEXT,
    barcode TEXT,
    name TEXT,
    qty REAL NOT NULL DEFAULT 1,
    ms_order_id TEXT,
    marks_count INTEGER NOT NULL DEFAULT 0,
    pulled_at TEXT,
    status_group TEXT,
    work_state TEXT,
    accepted_at TEXT,
    deadline_at TEXT,
    track TEXT,
    warehouse TEXT,
    image TEXT,
    supply_ext TEXT,
    trbx_ext TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (cabinet_id) REFERENCES cabinets(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS shipments_cab_kind_ext
    ON shipments (cabinet_id, kind, ext_id);
CREATE INDEX IF NOT EXISTS shipments_client_day
    ON shipments (client_id, shipped_at);

-- Поставки FBS Wildberries. Заводим у площадки, храним у себя, чтобы сборщик
-- видел, что уже собрано и в какое грузоместо уложено.
CREATE TABLE IF NOT EXISTS wb_supplies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    cabinet_id INTEGER NOT NULL,
    ext_id TEXT NOT NULL,
    name TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    author TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (cabinet_id) REFERENCES cabinets(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS wb_supplies_cab_ext
    ON wb_supplies (cabinet_id, ext_id);

CREATE TABLE IF NOT EXISTS wb_boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supply_id INTEGER NOT NULL,
    ext_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (supply_id) REFERENCES wb_supplies(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS wb_boxes_supply_ext
    ON wb_boxes (supply_id, ext_id);

CREATE TABLE IF NOT EXISTS shipment_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    gtin TEXT,
    article TEXT,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE INDEX IF NOT EXISTS shipment_marks_ship
    ON shipment_marks (shipment_id);
