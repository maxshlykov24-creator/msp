// Доменные типы кассы. Форма приближена к amoCRM (сделка) + МойСклад (товар),
// чтобы один и тот же тип использовался и фронтом (apps/web), и бэком (apps/api).

export type FunnelType = "offline" | "online" | "defects" | "return";

export type DealKind =
  | "sale"
  | "company"
  | "cert_plastic"
  | "rental"
  | "deferred"
  | "promise"
  | "no_sliv"
  | "sliv"
  | "cert_digital"
  | "delivery"
  | "defect"
  | "drycleaning"
  | "resew"
  | "wrong_size"
  /** Некорректная бирка: не тот размер/артикул на этикетке (созвон 20.08). */
  | "wrong_label"
  | "refund"
  | "exchange";

export type Store = "На Бауманской" | "На Пятницкой" | "Онлайн-магазин";

export type PaymentKind = "cash" | "card" | "account" | "certificate";

export interface PaymentMethod {
  id: string;
  label: string;
  kind: PaymentKind;
}

export interface Payment {
  id: string;
  methodId: string;
  amount: number;
  certificateNumber?: string;
  /** Дата этой оплаты YYYY-MM-DD (когда внесли платёж). */
  paidAt?: string;
}

/**
 * Одна строка выдачи денег (сдача, чаевые, возврат). Сумма выплаты всегда
 * раскладывается полностью: часть наличными, часть переводом, часть —
 * строкой «Переведёт Mansband», которая уходит в очередь Эдвина.
 */
export interface Payout {
  methodId: string;
  amount: number;
}

/** Остаток по одному складу (кэш МС или live-отчёт). */
export interface WarehouseStockLine {
  warehouseMsId: string;
  name: string;
  /** Доступно = остаток − резерв */
  available: number;
  reserve: number;
  /** Физический остаток на складе */
  stock: number;
}

export interface Product {
  id: string;
  name: string;
  sku: string;
  category: string;
  price: number;
  store: string;
  stock: number;
  barcode?: string;
  /** Остатки по складам — приходит из /catalog/search (локальный кэш). */
  warehouses?: WarehouseStockLine[];
}

/**
 * Статус позиции в заявке (вместо этапов «Ждет товар» / «Товар в магазине» на сделке).
 * Забронирован = есть в магазине, ещё не отложен физически.
 */
export type CartItemStatus = "waiting" | "in_store" | "booked" | "reserved";

export interface CartItem {
  productId: string;
  name: string;
  price: number;
  qty: number;
  /** Группа МойСклад: по ней форма продажи понимает, что позиция — костюм (САР). */
  category?: string;
  barcode?: string;
  discountPct?: number;
  discountRub?: number;
  isGift?: boolean;
  noPrice?: boolean; // для аренды/дефектов
  /** Позиция к возврату (обмен): уходит в salesreturn, не в отгрузку. */
  isReturn?: boolean;
  /** Статус товара в заявке (ожидание / в магазине / бронь / отложен). */
  itemStatus?: CartItemStatus;
  /** Группа костюма: общий id у пиджака/брюк/жилета одной вариации в чеке (созвон 09.09). */
  suitGroupId?: string;
  /** true — `price` сейчас разнесённая цена костюма из матрицы, а не цена МойСклад. */
  suitPriceApplied?: boolean;
  /** Консультант явно разобрал костюм на части — автосклейка не трогает эти строки. */
  suitSplit?: boolean;
  /** Цена МойСклад до разнесения — чтобы «Разделить» вернул исходную цену части. */
  suitOriginalPrice?: number;
}

export interface Consultant {
  id: string;
  name: string;
  role: "consultant" | "callmanager" | "supply" | "rop" | "finance";
}

export type ChangeStatus = "issued" | "pending";

// Запись истории действий по заявке (таймлайн в карточке)
export interface HistoryEntry {
  at: string; // ISO-датавремя
  who: string; // консультант / система
  action: string; // что произошло
}

export interface Deal {
  id: string;
  number: number;
  createdAt: string;
  funnel: FunnelType;
  kind: DealKind;
  consultant: string;
  referredBy?: string;
  callManager?: string;
  clientName: string;
  clientPhone: string;
  email?: string;
  store: Store;
  storeAddress?: string;
  channel?: string;
  purpose?: string;
  /** Сарафан: телефон друга, который направил клиента. */
  saryPhone?: string;
  /** Имя друга — как нашли по телефону в базе / amoCRM. */
  saryClient?: string;
  /** Использованный бонус сарафана в чеке (обычно 1000 ₽). */
  saryBonus?: number;
  /** Когда о сарафане написали примечание в amoCRM (защита от дублей). */
  saryNotedAt?: string;
  items: CartItem[];
  payments: Payment[];
  stage: string;
  certificateNumber?: string;
  guestName?: string;
  rentalFrom?: string;
  rentalTo?: string;
  issueDate?: string;
  returnDate?: string;
  reservedUntil?: string;
  comment?: string;
  meetingDate?: string;
  tips?: number;
  tipsDestination?: string;
  /** Способ, которым консультант выдал чаевые сам (id из PAYMENT_METHODS). */
  tipsMethodId?: string;
  /** Раскладка чаевых по способам, включая строку «Переведёт Mansband». */
  tipsPayouts?: Payout[];
  checkDiscount?: number;
  companyName?: string;
  invoiceNo?: string;
  invoicePaid?: boolean;
  issued?: boolean;
  managerName?: string;
  managerPhone?: string;
  deferredUntil?: string;
  actualUntil?: string;
  receivedAt?: string;
  validUntil?: string;
  photoAttached?: boolean;
  deliveryCity?: string;
  recipientName?: string;
  recipientPhone?: string;
  linkedDealNumber?: number;
  returnStatus?: "issued" | "pending";
  returnDestination?: string;
  /** Раскладка возврата по способам, включая строку «Переведёт Mansband». */
  returnPayouts?: Payout[];
  changeStatus?: "issued" | "pending";
  changeDestination?: string;
  /** Способ, которым консультант выдал сдачу сам (id из PAYMENT_METHODS). */
  changeMethodId?: string;
  /** Раскладка сдачи по способам, включая строку «Переведёт Mansband». */
  changePayouts?: Payout[];
  paymentDate?: string;
  history?: HistoryEntry[];
  total: number;
  paid: number;
  // Связь с amoCRM / МойСклад (заполняется бэкендом)
  amoLeadId?: number;
  msOrderId?: string;
  msDemandId?: string; // отгрузка
  paymentStatus?: string;
  /** Клиентский ключ повтора; номер сделки всегда назначает сервер. */
  idempotencyKey?: string;
  syncStatus?: "pending" | "synced" | "failed";
  syncError?: string;
  subtotal?: number;
  discountTotal?: number;
  atelierAmount?: number;
  deliveryAmount?: number;
  closingDocumentsRequired?: boolean;
  invoiceDate?: string;
  /** Дата внесения счёта в кассу — ставит сервер, Эдвин её не правит. */
  invoiceEnteredAt?: string;
  invoiceStatus?: "draft" | "awaiting_payment" | "paid";
  /** Закрывающие документы по продаже компании — очередь Миши. */
  documentsStatus?: "pending" | "ready" | "handed";
  /** Оплата ателье по продаже компании — очередь Миши, только если есть сумма ателье. */
  atelierStatus?: "pending" | "paid";
  rentalStatus?: "reserved" | "paid" | "issued" | "returned" | "closed";
  rentalIssuedAt?: string;
  rentalReturnedAt?: string;
  rentalDeposit?: number;
}

export type CertStatus = "active" | "used" | "expired";

export interface Certificate {
  number: string;
  nominal: number;
  balance: number;
  status: CertStatus;
  type: "plastic" | "digital";
  guestName?: string;
  buyerDealNumber?: number;
  issuedAt: string;
  validUntil: string;
}

/**
 * Виды позиций финансовых очередей. Цепочка по продаже компании идёт строго
 * по одной задаче (правки владельца 10.08.2026, пп.8–9):
 * invoice → invoice_check → documents → documents_hand → atelier.
 */
export type QueueKind =
  | "change"
  | "tips"
  | "refund"
  | "invoice"
  /** Счёт выставлен — Эдвин ждёт и проверяет оплату. */
  | "invoice_check"
  /** Миша готовит закрывающие документы. */
  | "documents"
  /** Миша передаёт закрывающие документы. */
  | "documents_hand"
  | "atelier"
  /** Эдвин выдаёт зарплату консультантам за прошлую неделю (созвон 20.08). */
  | "salary";

/** Одна выдача по позиции очереди: часть налом, часть переводом (п.6–7). */
export interface QueuePayout {
  id: string;
  amount: number;
  methodId: string;
  issuedBy: string;
  issuedAt: string;
  note?: string;
}

export interface QueueItem {
  id: string;
  kind: QueueKind;
  dealNumber: number;
  client: string;
  amount: number;
  destination: string; // телефон/карта + банк
  status: ChangeStatus;
  /** Сколько уже выдано; меньше amount — позиция остаётся в очереди. */
  issuedAmount: number;
  payouts: QueuePayout[];
  createdAt: string;
  issuedAt?: string;
  issuedBy?: string;
  issueMethod?: string;
  issueAccount?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Статусы САР: pending — к отправке, sent — переведена (есть скрин и дата),
 * in_check — учтена бонусом −1000 ₽ прямо в чеке, переводить нечего.
 */
export type SaryStatus = "pending" | "sent" | "in_check";

export interface SaryPayout {
  id: string;
  client: string;
  phone: string;
  amount: number;
  reason: string;
  refDealNumber?: number;
  status: SaryStatus;
  screenshotAttached: boolean;
  createdAt: string;
  /** Когда пачка фактически отправлена (только для status = sent). */
  sentAt?: string;
  /**
   * Порядковый номер САР внутри заявки: костюмов в чеке несколько — САР столько же
   * (созвон 04.09). У ручных выплат без заявки всегда 1.
   */
  seq: number;
  /** Телефон друга нашёлся в базе. false — САР проходит с меткой «не найдено». */
  phoneFound: boolean;
}

// ── Аутентификация / пользователи ───────────────────────────────────

export type UserRole =
  | "consultant"
  | "logist"
  | "finance"
  | "crm"
  | "rop"
  | "admin"
  | "seller";

export interface User {
  id: string;
  login: string;
  name: string;
  role: UserRole;
  mustChangePassword: boolean;
  /** Магазин сотрудника: нужен ведомости доступов и подсказкам в очередях. */
  store?: string;
}

/**
 * Ответ на создание пользователя или сброс пароля: временный пароль виден
 * один раз, в базе лежит только хеш (созвон 04.09, ведомость доступов).
 */
export interface UserWithTempPassword {
  user: User;
  tempPassword: string;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export type TaskKind =
  | "movement"
  /** Приёмка перемещения принимающим магазином: создаёт документ move в МойСклад. */
  | "movement_accept"
  | "reserve"
  /** Срок отложки вышел: колл-менеджер связывается с клиентом до снятия. */
  | "reserve_call"
  | "unreserve"
  | "assemble_cdek"
  /** Заказ собран — колл-менеджер вызывает курьера СДЭК. */
  | "call_courier"
  | "take_to_cdek"
  | "pickup_from_cdek"
  /** Сарафан: перевести 1000 ₽ тому, кто направил клиента (очередь колл-менеджера). */
  | "sary_send"
  | "barcode"
  | "manual_check";

export type TaskAssigneeRole = "consultant" | "logist" | "crm";

export interface Task {
  id: string;
  kind: TaskKind;
  dealNumber?: number;
  dealItemId?: string;
  store?: string;
  assigneeRole: TaskAssigneeRole;
  status: "pending" | "done" | "cancelled";
  title: string;
  idempotencyKey?: string;
  metadata?: Record<string, unknown>;
  createdBy: string;
  completedBy?: string;
  createdAt: string;
  completedAt?: string;
}

/** Происхождение записи истории: действие человека или автоматика системы. */
export type AuditSource = "manual" | "auto";

export interface AuditEntry {
  id: string;
  actorId?: string;
  actorName: string;
  actorRole: UserRole;
  action: string;
  entityType: string;
  entityId: string;
  /** manual — действие под учёткой; auto — задачи формулы, синк amo/МС, автозакрытия. */
  source: AuditSource;
  before?: unknown;
  after?: unknown;
  metadata?: Record<string, unknown>;
  createdAt: string;
}

/** Часть расхода, оплаченная конкретным способом (п.11). */
export interface ExpenseSplit {
  methodId: string;
  amount: number;
}

export interface Expense {
  id: string;
  category: string;
  account: string;
  amount: number;
  /** Разбивка по способам оплаты. Пусто — весь расход прошёл через `account`. */
  splits?: ExpenseSplit[];
  description?: string;
  spentAt: string;
  createdBy: string;
}

export interface AccountBalance {
  account: string;
  balance: number;
  updatedAt: string;
}

// ── Костюмы и комплектность ─────────────────────────────────────────

/** Часть костюма. Пиджак ведущий: один пиджак — один костюм. */
export type SuitPart = "jacket" | "trousers" | "vest";

/**
 * Линия внутри вариации. Одна вариация может содержать и смокинговые виды, и
 * обычные — тогда это две разные модели, склеивать их нельзя.
 */
export type SuitLine = "smoking" | "regular";

/** Цельные костюмы этого размера на одном складе, без свода между складами. */
export interface SuitWarehouseQty {
  name: string;
  whole: number;
}

/** Строка размера внутри модели: что цельное, чего не хватает. */
export interface SuitSizeRow {
  size: string;
  /** Остаток по частям в этом размере. */
  parts: Record<SuitPart, number>;
  /** Костюмов, собираемых в размер к размеру. */
  whole: number;
  /** Костюмов, собираемых со сдвигом размера в пределах допуска. */
  tolerant: number;
  /** Изделия без пары вовсе или со сдвигом вне допуска. */
  orphans: SuitOrphan[];
  /** Цельные на каждом складе, где в этом размере вообще что-то лежит. */
  warehouses: SuitWarehouseQty[];
}

/** Изделие без пары: чего не хватает и где парную часть можно забрать. */
export interface SuitOrphan {
  part: SuitPart;
  qty: number;
  /** Каких частей состава не хватает в допуске размеров. */
  missing: SuitPart[];
  /** Размеры, в которых недостающая часть вообще есть. */
  nearestSizes: string[];
  /** Парная часть на других складах: по ней собирается перемещение. */
  pairLocations: Array<{ part: SuitPart; size: string; warehouse: string; qty: number; msId: string }>;
}

/** Модель костюма: строка экрана «Костюмы». */
export interface SuitModel {
  modelId: string;
  variation: string;
  title: string;
  color: string | null;
  pattern: string | null;
  fit: string | null;
  height: string | null;
  line: SuitLine;
  /** Состав модели: есть жилет — тройка. */
  composition: SuitPart[];
  /** Внутри вариации цвет или узор частей расходятся: взяты по пиджаку. */
  mixed: boolean;
  whole: number;
  tolerant: number;
  orphans: number;
  /** Остаток на физических складах полупарков — не вычисленный некомплект. */
  onHalfSetWarehouse: number;
  sizes: SuitSizeRow[];
}

export interface SuitCompleteness {
  warehouse: string | null;
  tolerance: number;
  totals: { models: number; whole: number; tolerant: number; orphans: number; items: number };
  models: SuitModel[];
}

/** Запись журнала разбитых костюмов. */
export interface SuitBreak {
  id: string;
  at: string;
  store: string;
  consultant: string;
  refDealNumber: string | null;
  variation: string;
  title: string;
  soldPart: SuitPart;
  size: string | null;
  /** Части, оставшиеся без пары после продажи. */
  leftParts: SuitPart[];
  source: "sale" | "snapshot";
}

/**
 * Правило цены костюма из прайса (созвон 09.09): вид пиджака / группа МойСклад
 * + число предметов + ростовка + линия дают цену костюма целиком. Матрица
 * лежит в коде (`SUIT_PRICE_RULES_DEFAULT`), а `priceRub`/`active` каждого
 * правила можно переопределить в «Настройках» без релиза — оверрайд хранится
 * в `app_settings.suitPriceOverrides` по `id` правила.
 */
export interface SuitPriceRule {
  id: string;
  label: string;
  priceRub: number;
  active: boolean;
  match: {
    /** Путь группы МойСклад (products.category) содержит одну из подстрок. */
    categoryIncludes?: string[];
    /** Название вида пиджака (до скобки характеристик) содержит одну из подстрок. */
    jacketNameIncludes?: string[];
    line?: SuitLine;
    height?: "6" | "8";
    /** 2 — без жилета, 3 — с жилетом. Не задано — любое число предметов. */
    pieces?: 2 | 3;
  };
}

/** Вход для подбора цены костюма — характеристики собранной в чеке группы. */
export interface SuitPriceMatchInput {
  jacketName: string;
  category: string;
  pieces: 2 | 3;
  line: SuitLine;
  height: string;
}

/** Возраст остатка по дате оприходования. */
export interface StockAgeBucket {
  bucket: "0-3м" | "3-6м" | "6-12м" | "12м+";
  items: number;
  models: number;
}

export interface StockStats {
  warehouse: string | null;
  buckets: StockAgeBucket[];
  /** Светофор: доля остатка в красной зоне по возрасту. */
  light: "green" | "yellow" | "red";
  oldestEnterDate: string | null;
  /** Перекос сеток: частей без пары своего размера. */
  gridSkew: { jacketsWithoutTrousers: number; trousersWithoutJackets: number };
}

// ── Настройки приложения (порог САР и т.п.) ─────────────────────────

/** Настраиваемые параметры кассы (таблица app_settings). Правят rop / admin. */
export interface AppSettings {
  /** Минимальная сумма чека для начисления САР без костюма, ₽ (дефолт 20 000). */
  saryMinCheck: number;
  /** Группы МойСклад, считающиеся костюмом: по ним берётся количество САР. */
  sarySuitGroups: string[];
  /** Допуск размеров верха и низа, при котором костюм ещё продаётся как костюм. */
  suitSizeTolerance: number;
  /** Слова в названии вида, по которым определяется часть костюма. */
  suitPartKeywords: Record<SuitPart, string[]>;
  /** Возраст партии в днях, с которого остаток жёлтый и красный. */
  stockAgeYellowDays: number;
  stockAgeRedDays: number;
  /**
   * Переопределение цены/активности правил матрицы костюмов по `id` правила.
   * Логика подбора (match) остаётся в коде — правит только владелец через
   * «Настройки», без риска сломать матчинг.
   */
  suitPriceOverrides: Record<string, { priceRub: number; active: boolean }>;
}

// ── Зарплата и бонусы консультантов (созвон 20.08) ──────────────────

/**
 * Порог премии: от `from` (включительно) до `to` (исключительно, null = без верха).
 * Для конверсии значения в процентах (85, 87.001…), для UPT — штук на чек.
 */
export interface PayrollTier {
  from: number;
  to: number | null;
  /**
   * Премия (или штраф) за период в процентах от выручки консультанта за период
   * (созвон 04.09: «чтобы премия рассчитывалась не в сумме, а в проценте»).
   */
  bonusPct: number;
}

/** Единые условия для всех консультантов: «условия труда для всех одинаковые». */
export interface PayrollSettings {
  /** Процент от выручки консультанта за день (например 5 = 5%). */
  revenuePct: number;
  /** Обеспечительная ставка за день, ₽: если % от выручки меньше — платим её. */
  dailyFloor: number;
  conversionTiers: PayrollTier[];
  uptTiers: PayrollTier[];
  /** Штрафы за период: те же пороги, но вычитаются из итога (созвон 04.09). */
  penaltyConversionTiers: PayrollTier[];
  penaltyUptTiers: PayrollTier[];
  updatedBy?: string;
  updatedAt?: string;
}

/** Один рабочий день консультанта в расчёте ЗП. */
export interface PayrollDay {
  date: string; // YYYY-MM-DD
  revenue: number;
  /** revenue × revenuePct / 100 */
  pctAmount: number;
  /** max(pctAmount, dailyFloor) */
  payout: number;
  /** true — сработала обеспечительная ставка. */
  floorApplied: boolean;
}

/** Расчёт ЗП одного консультанта за период. */
export interface PayrollRow {
  consultant: string;
  days: PayrollDay[];
  basePay: number;
  /** Выручка за период — база для премий и штрафов в процентах. */
  periodRevenue: number;
  conversionPct: number | null;
  /** Процент премии по конверсии, взятый из порога. */
  conversionBonusPct: number;
  conversionBonus: number;
  upt: number | null;
  uptBonusPct: number;
  uptBonus: number;
  /** Штрафы за период (созвон 04.09): тоже процент от выручки. */
  conversionPenaltyPct: number;
  conversionPenalty: number;
  uptPenaltyPct: number;
  uptPenalty: number;
  /** basePay + премии − штрафы, не ниже нуля. */
  total: number;
}

export interface PayrollReport {
  from: string;
  to: string;
  settings: PayrollSettings;
  rows: PayrollRow[];
}

// ── Реалтайм (WebSocket) ────────────────────────────────────────────

export type WsEventType =
  | "deal.created"
  | "deal.updated"
  | "deal.stage_changed"
  | "catalog.updated"
  | "certificate.updated"
  | "queue.updated"
  | "sary.updated"
  | "ping";

export interface WsEvent<T = unknown> {
  type: WsEventType;
  at: string;
  payload: T;
}
