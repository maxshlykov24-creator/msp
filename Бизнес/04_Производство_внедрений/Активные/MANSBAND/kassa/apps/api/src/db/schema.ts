import {
  pgTable,
  text,
  uuid,
  timestamp,
  integer,
  boolean,
  jsonb,
  numeric,
  bigint,
  uniqueIndex,
  index,
} from "drizzle-orm/pg-core";

// ── Пользователи / авторизация ──────────────────────────────────────
export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  login: text("login").notNull().unique(),
  name: text("name").notNull(),
  passwordHash: text("password_hash").notNull(),
  role: text("role").notNull().default("seller"), // admin | seller
  mustChangePassword: boolean("must_change_password").notNull().default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Маппинг справочников amoCRM (воронки/этапы/кастом-поля) ──────────
export const amoMeta = pgTable(
  "amo_meta",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    kind: text("kind").notNull(), // pipeline | status | field | enum
    amoId: bigint("amo_id", { mode: "number" }).notNull(),
    parentId: bigint("parent_id", { mode: "number" }), // pipeline для статуса, field для enum
    name: text("name").notNull(),
    payload: jsonb("payload"), // сырой объект из amo
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byKind: index("amo_meta_kind_idx").on(t.kind),
    uniq: uniqueIndex("amo_meta_kind_amo_idx").on(t.kind, t.amoId),
  })
);

// ── Кэш каталога МойСклад ───────────────────────────────────────────
export const products = pgTable(
  "products",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    msId: text("ms_id").notNull().unique(), // assortment id
    msMetaHref: text("ms_meta_href").notNull(),
    msType: text("ms_type").notNull().default("product"), // product | variant | service | bundle
    name: text("name").notNull(),
    article: text("article"),
    code: text("code"),
    barcode: text("barcode"),
    category: text("category"),
    price: integer("price").notNull().default(0), // в копейках
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byName: index("products_name_idx").on(t.name),
    byArticle: index("products_article_idx").on(t.article),
    byBarcode: index("products_barcode_idx").on(t.barcode),
  })
);

// Остатки по складам (кэш; точное значение перечитывается on-demand при продаже)
export const stock = pgTable(
  "stock",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    productMsId: text("product_ms_id").notNull(),
    warehouseMsId: text("warehouse_ms_id").notNull(),
    warehouseName: text("warehouse_name").notNull(),
    quantity: numeric("quantity").notNull().default("0"),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    uniq: uniqueIndex("stock_product_wh_idx").on(t.productMsId, t.warehouseMsId),
  })
);

// Резолв meta МойСклад: организация, склады, контрагент розницы, счета/кассы
export const msRefs = pgTable("ms_refs", {
  id: uuid("id").defaultRandom().primaryKey(),
  kind: text("kind").notNull(), // organization | store | counterparty | account | cashier
  key: text("key").notNull(), // логический ключ (имя шоурума и т.п.)
  msId: text("ms_id").notNull(),
  metaHref: text("meta_href").notNull(),
  name: text("name").notNull(),
  payload: jsonb("payload"),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Заявки (зеркало amoCRM-сделок + локальные служебные) ────────────
export const deals = pgTable(
  "deals",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    number: integer("number").notNull(),
    amoLeadId: bigint("amo_lead_id", { mode: "number" }),
    msOrderId: text("ms_order_id"),
    msDemandId: text("ms_demand_id"),
    data: jsonb("data").notNull(), // полный объект Deal (shared)
    stage: text("stage").notNull().default("Новая заявка"),
    paymentStatus: text("payment_status"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byNumber: uniqueIndex("deals_number_idx").on(t.number),
    byAmo: index("deals_amo_idx").on(t.amoLeadId),
  })
);

// ── Журнал платежей (неизменяемый ledger) ───────────────────────────
export const ledger = pgTable(
  "ledger",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    dealNumber: integer("deal_number").notNull(),
    methodId: text("method_id").notNull(),
    kind: text("kind").notNull(), // cash | card | account | certificate | refund
    amount: integer("amount").notNull(), // копейки (может быть отрицательным для возврата)
    msPaymentId: text("ms_payment_id"),
    idempotencyKey: text("idempotency_key").notNull(),
    createdBy: text("created_by").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    uniqIdem: uniqueIndex("ledger_idem_idx").on(t.idempotencyKey, t.methodId, t.amount),
    byDeal: index("ledger_deal_idx").on(t.dealNumber),
  })
);

// ── Сертификаты ─────────────────────────────────────────────────────
export const certificates = pgTable("certificates", {
  number: text("number").primaryKey(),
  nominal: integer("nominal").notNull(),
  balance: integer("balance").notNull(),
  status: text("status").notNull().default("active"), // active | used | expired
  type: text("type").notNull().default("plastic"),
  guestName: text("guest_name"),
  buyerDealNumber: integer("buyer_deal_number"),
  issuedAt: text("issued_at").notNull(),
  validUntil: text("valid_until").notNull(),
});

// ── Очередь Эдвина (сдача/возврат «не выдано») ──────────────────────
export const queue = pgTable("queue", {
  id: uuid("id").defaultRandom().primaryKey(),
  kind: text("kind").notNull(), // change | refund
  dealNumber: integer("deal_number").notNull(),
  client: text("client").notNull(),
  amount: integer("amount").notNull(),
  destination: text("destination").notNull(),
  status: text("status").notNull().default("pending"), // pending | issued
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Сары (реферальные выплаты) ──────────────────────────────────────
export const sary = pgTable("sary", {
  id: uuid("id").defaultRandom().primaryKey(),
  client: text("client").notNull(),
  phone: text("phone").notNull(),
  amount: integer("amount").notNull(),
  reason: text("reason").notNull(),
  refDealNumber: integer("ref_deal_number"),
  status: text("status").notNull().default("pending"), // pending | sent
  screenshotAttached: boolean("screenshot_attached").notNull().default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Идемпотентность операций (продажа/отгрузка/фото) ────────────────
export const idempotencyKeys = pgTable("idempotency_keys", {
  key: text("key").primaryKey(),
  scope: text("scope").notNull(), // sale | file | demand
  result: jsonb("result"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Закрытие смены ──────────────────────────────────────────────────
export const shifts = pgTable("shifts", {
  id: uuid("id").defaultRandom().primaryKey(),
  store: text("store").notNull(),
  openedBy: text("opened_by").notNull(),
  openedAt: timestamp("opened_at", { withTimezone: true }).notNull().defaultNow(),
  closedAt: timestamp("closed_at", { withTimezone: true }),
  expectedCash: integer("expected_cash"),
  countedCash: integer("counted_cash"),
  reconciled: boolean("reconciled").notNull().default(false),
  payload: jsonb("payload"),
});
