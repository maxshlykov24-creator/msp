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
  // Магазин сотрудника: ведомость доступов и подсказки в очередях (созвон 04.09).
  store: text("store"),
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

// Дерево групп товаров МойСклад — эталон иерархии категорий для кассы (п.28).
export const productFolders = pgTable(
  "product_folders",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    msId: text("ms_id").notNull().unique(),
    name: text("name").notNull(),
    // Полный путь группы: путь родителя + собственное имя («Костюмы/Тройки»).
    path: text("path").notNull(),
    parentMsId: text("parent_ms_id"),
    level: integer("level").notNull().default(1),
    archived: boolean("archived").notNull().default(false),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byPath: index("product_folders_path_idx").on(t.path),
    byParent: index("product_folders_parent_idx").on(t.parentMsId),
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
    idempotencyKey: text("idempotency_key"),
    syncStatus: text("sync_status").notNull().default("pending"),
    syncError: text("sync_error"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byNumber: uniqueIndex("deals_number_idx").on(t.number),
    byAmo: index("deals_amo_idx").on(t.amoLeadId),
    byIdempotency: uniqueIndex("deals_idempotency_idx").on(t.idempotencyKey),
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

// ── Очередь Эдвина (сдача/чаевые/возврат «не выдано») ───────────────
export const queue = pgTable("queue", {
  id: uuid("id").defaultRandom().primaryKey(),
  kind: text("kind").notNull(), // change | tips | refund | invoice
  dealNumber: integer("deal_number").notNull(),
  client: text("client").notNull(),
  amount: integer("amount").notNull(),
  destination: text("destination").notNull(),
  status: text("status").notNull().default("pending"), // pending | issued
  // Сумма, выданная частями (копейки). status = issued, когда достигла amount.
  issuedAmount: integer("issued_amount").notNull().default(0),
  metadata: jsonb("metadata"),
  issuedAt: timestamp("issued_at", { withTimezone: true }),
  issuedBy: text("issued_by"),
  issueMethod: text("issue_method"),
  issueAccount: text("issue_account"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// Частичные выдачи по позиции очереди: часть налом, часть переводом.
export const queuePayouts = pgTable(
  "queue_payouts",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    queueId: uuid("queue_id").notNull(),
    amount: integer("amount").notNull(), // копейки
    methodId: text("method_id").notNull(),
    issuedBy: text("issued_by").notNull(),
    issuedAt: timestamp("issued_at", { withTimezone: true }).notNull().defaultNow(),
    note: text("note"),
  },
  (t) => ({ byQueue: index("queue_payouts_queue_idx").on(t.queueId) })
);

// ── Очереди операций и положение позиций внутри заявки ─────────────
export const tasks = pgTable(
  "tasks",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    kind: text("kind").notNull(),
    dealNumber: integer("deal_number"),
    dealItemId: text("deal_item_id"),
    store: text("store"),
    assigneeRole: text("assignee_role").notNull(),
    status: text("status").notNull().default("pending"),
    title: text("title").notNull(),
    idempotencyKey: text("idempotency_key"),
    metadata: jsonb("metadata"),
    createdBy: text("created_by").notNull(),
    completedBy: text("completed_by"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    completedAt: timestamp("completed_at", { withTimezone: true }),
  },
  (t) => ({
    byStatusRole: index("tasks_status_role_idx").on(t.status, t.assigneeRole),
    byDeal: index("tasks_deal_idx").on(t.dealNumber),
    byIdempotency: uniqueIndex("tasks_idempotency_idx").on(t.idempotencyKey),
  })
);

export const dealItemState = pgTable(
  "deal_item_state",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    dealNumber: integer("deal_number").notNull(),
    itemId: text("item_id").notNull(),
    state: text("state").notNull().default("in_store"),
    location: text("location"),
    metadata: jsonb("metadata"),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    uniq: uniqueIndex("deal_item_state_deal_item_idx").on(t.dealNumber, t.itemId),
  })
);

// ── Финансы Эдвина ──────────────────────────────────────────────────
export const expenses = pgTable(
  "expenses",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    category: text("category").notNull(),
    // Основной источник оплаты. При разбивке — способ с наибольшей суммой.
    account: text("account").notNull(),
    amount: integer("amount").notNull(),
    // Разбивка расхода по способам оплаты: [{ methodId, amount }] в копейках.
    splits: jsonb("splits"),
    description: text("description"),
    spentAt: timestamp("spent_at", { withTimezone: true }).notNull().defaultNow(),
    createdBy: text("created_by").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({ byDate: index("expenses_spent_at_idx").on(t.spentAt) })
);

export const accountBalances = pgTable("account_balances", {
  account: text("account").primaryKey(),
  balance: integer("balance").notNull().default(0),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  updatedBy: text("updated_by").notNull(),
});

// Append-only: в приложении отсутствуют UPDATE/DELETE для этой таблицы.
export const auditLog = pgTable(
  "audit_log",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    actorId: uuid("actor_id"),
    actorName: text("actor_name").notNull(),
    actorRole: text("actor_role").notNull(),
    action: text("action").notNull(),
    entityType: text("entity_type").notNull(),
    entityId: text("entity_id").notNull(),
    // manual — действие под учёткой; auto — формула задач, синк amo/МС, cron.
    source: text("source").notNull().default("manual"),
    before: jsonb("before"),
    after: jsonb("after"),
    metadata: jsonb("metadata"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    byEntity: index("audit_entity_idx").on(t.entityType, t.entityId),
    byCreatedAt: index("audit_created_at_idx").on(t.createdAt),
  })
);

// ── Сары (реферальные выплаты) ──────────────────────────────────────
export const sary = pgTable(
  "sary",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    client: text("client").notNull(),
    phone: text("phone").notNull(),
    amount: integer("amount").notNull(),
    reason: text("reason").notNull(),
    refDealNumber: integer("ref_deal_number"),
    // Номер САР внутри заявки: костюмов в чеке несколько — САР столько же
    // (созвон 04.09). Вместе с ref_deal_number даёт идемпотентность начисления.
    seq: integer("seq").notNull().default(1),
    // Телефон друга не нашёлся в базе: САР всё равно проходит, но с меткой,
    // чтобы колл-менеджер проверил номер перед переводом (созвон 04.09).
    phoneFound: boolean("phone_found").notNull().default(true),
    // pending — к отправке; sent — переведена (скрин + sent_at);
    // in_check — учтена бонусом в чеке, переводить нечего (созвон 20.08).
    status: text("status").notNull().default("pending"),
    screenshotAttached: boolean("screenshot_attached").notNull().default(false),
    sentAt: timestamp("sent_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    uniqDealSeq: uniqueIndex("sary_deal_seq_idx").on(t.refDealNumber, t.seq),
  })
);

// ── Настройки кассы (порог САР и другие параметры, правят rop/admin) ─
export const appSettings = pgTable("app_settings", {
  key: text("key").primaryKey(),
  value: jsonb("value").notNull(),
  updatedBy: text("updated_by").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

// ── Настройки мотивации консультантов (созвон 20.08) ────────────────
// Append-only история: действующая запись — последняя по created_at.
export const payrollSettings = pgTable("payroll_settings", {
  id: uuid("id").defaultRandom().primaryKey(),
  // Процент от выручки за день, в сотых долях процента не нужен — храним как numeric.
  revenuePct: numeric("revenue_pct").notNull().default("5"),
  // Обеспечительная ставка за день, копейки.
  dailyFloor: integer("daily_floor").notNull().default(500000),
  // Пороги премий: [{ from, to, bonusPct }], bonusPct — процент от выручки
  // консультанта за период (созвон 04.09; до этого премия задавалась в рублях).
  conversionTiers: jsonb("conversion_tiers").notNull(),
  uptTiers: jsonb("upt_tiers").notNull(),
  // Штрафы за период: та же структура, вычитаются из итога.
  penaltyConversionTiers: jsonb("penalty_conversion_tiers").notNull().default([]),
  penaltyUptTiers: jsonb("penalty_upt_tiers").notNull().default([]),
  createdBy: text("created_by").notNull(),
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
