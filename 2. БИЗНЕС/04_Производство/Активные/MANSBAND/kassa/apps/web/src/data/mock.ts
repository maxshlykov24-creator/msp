import type {
  Certificate,
  Consultant,
  Deal,
  DealKind,
  Product,
  QueueItem,
  SaryPayout,
  Store,
} from "./types";
import { AD_SOURCES } from "@kassa/shared";

// ── Справочники (реальные значения из amoCRM / ТЗ) ──────────────────

export const STORES = ["На Бауманской", "На Пятницкой", "Онлайн-магазин"] as const;

// Адреса магазинов (авто-подстановка в форму и карточку заявки)
export const STORE_ADDRESS: Record<Store, string> = {
  "На Бауманской": "На Бауманской",
  "На Пятницкой": "На Пятницкой",
  "Онлайн-магазин": "Онлайн-магазин",
};

export const CONSULTANTS: Consultant[] = [
  { id: "c1", name: "Матвей", role: "consultant" },
  { id: "c2", name: "Женя", role: "consultant" },
  { id: "c3", name: "Гриша", role: "consultant" },
  { id: "c4", name: "Саша", role: "consultant" },
  { id: "c5", name: "Арсен", role: "consultant" },
  { id: "c6", name: "Илья", role: "supply" },
  { id: "m1", name: "Сергей (call)", role: "callmanager" },
  { id: "f1", name: "Эдвин", role: "finance" },
];

// Способы приёма денег — единый справочник в @kassa/shared (созвон 29.07.2026).
export { PAYMENT_METHODS, PAYMENT_METHOD_GROUPS, findPaymentMethod, paymentMethodLabel } from "@kassa/shared";

// Каналы продаж (amoCRM, поле «Канал продаж»)
export const CHANNELS = AD_SOURCES;

// Цель покупки (amoCRM, поле «Цель покупки»)
export const PURPOSES = ["Свадьба", "Мероприятия", "Работа", "Повседнев"];

// Этапы воронки «Продажи» (amoCRM, pipeline 9601214) — без «Новая заявка» / «Взято в работу»
export const SALE_STAGES = [
  "Дано обещание",
  "Хочет прийти",
  "Ждет товар",
  "Товар в пути",
  "Товар в магазине",
  "Товар отложен",
  "Встреча назначена",
  "Аренда оплачена",
  "В аренде",
  "Возвращена",
  "Счёт запрошен",
  "Счет выставлен",
  "Оплачено",
  "Документы готовы",
  "Документы переданы",
  "Передан на сборку",
  "Собран",
  "Вызван курьер",
  "Отправлен",
  "Доставлен",
  "Не выкуплен",
  "Сертификат оплачен",
  "Успех",
  "Провал",
];

/** Этапы, скрытые в UI кассы (не показываем в селектах / не тянем в открытых). */
export const HIDDEN_STAGES = new Set([
  "Новая заявка",
  "Взято в работу",
  "Взята в работу",
  "Неразобранное",
]);

// Этапы по виду заявки — для корректной смены этапа в списке (DealModal).
// «Товар в магазине» есть в списке жизненного цикла (его ставит приёмка перемещения),
// но не в SAVE_STAGES форм — при создании заявки его не выбирают.
export const STAGES_BY_KIND: Record<DealKind, string[]> = {
  // Провал у продажи при создании не ставят — это слив / не слив.
  sale: ["Хочет прийти", "Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен", "Встреча назначена", "Успех"],
  company: [
    "Товар отложен",
    "Счёт запрошен",
    "Счет выставлен",
    "Оплачено",
    "Ждет товар",
    "Товар в пути",
    "Товар в магазине",
    "Документы готовы",
    "Документы переданы",
    "Успех",
    "Провал",
  ],
  // Пластик иногда нужно привезти из другой точки — «Ждет товар» нужен и здесь.
  cert_plastic: ["Сертификат оплачен", "Ждет товар", "Товар в пути", "Товар в магазине", "Успех", "Провал"],
  cert_digital: ["Сертификат оплачен", "Успех", "Провал"],
  rental: ["Аренда оплачена", "Ждет товар", "Товар в пути", "Товар в магазине", "В аренде", "Успех", "Провал"],
  deferred: ["Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен", "Успех", "Провал"],
  promise: ["Дано обещание", "Хочет прийти", "Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен", "Успех", "Провал"],
  no_sliv: ["Провал"],
  sliv: ["Встреча назначена", "Провал"],
  delivery: ["Передан на сборку", "Собран", "Вызван курьер", "Отправлен", "Доставлен", "Не выкуплен", "Успех", "Провал"],
  defect: ["Успех", "Провал"],
  drycleaning: ["Успех", "Провал"],
  resew: ["Успех", "Провал"],
  wrong_size: ["Успех", "Провал"],
  wrong_label: ["Успех", "Провал"],
  refund: ["Успех", "Провал"],
  exchange: ["Успех", "Провал"],
};

// ── Каталог товаров (категории — реальные из МойСклад) ──────────────

export const PRODUCTS: Product[] = [
  { id: "p1", name: "Костюм-тройка тёмно-синий", sku: "MB-3-001", category: "Костюмы / Тройки", price: 34900, store: "На Бауманской", stock: 4 },
  { id: "p2", name: "Костюм-двойка графит", sku: "MB-2-014", category: "Костюмы / Двойки", price: 28900, store: "На Бауманской", stock: 6 },
  { id: "p3", name: "Смокинг чёрный атлас", sku: "MB-SM-003", category: "Костюмы / Смокинги", price: 42900, store: "На Пятницкой", stock: 2 },
  { id: "p4", name: "Двубортный костюм синий", sku: "MB-DB-007", category: "Костюмы / Двубортные", price: 38900, store: "На Бауманской", stock: 3 },
  { id: "p5", name: "Сорочка белая приталенная", sku: "MB-SH-101", category: "Рубашки", price: 4500, store: "На Бауманской", stock: 25 },
  { id: "p6", name: "Сорочка голубая classic", sku: "MB-SH-118", category: "Рубашки", price: 4500, store: "На Пятницкой", stock: 18 },
  { id: "p7", name: "Галстук шёлковый бордо", sku: "MB-AC-220", category: "Аксессуары", price: 2900, store: "На Бауманской", stock: 30 },
  { id: "p8", name: "Бабочка чёрная атлас", sku: "MB-AC-231", category: "Аксессуары", price: 1900, store: "На Бауманской", stock: 22 },
  { id: "p9", name: "Платок-паше белый", sku: "MB-AC-240", category: "Аксессуары", price: 1200, store: "На Пятницкой", stock: 40 },
  { id: "p10", name: "Туфли дерби чёрные", sku: "MB-Sb-330", category: "Обувь", price: 12900, store: "На Бауманской", stock: 8 },
  { id: "p11", name: "Ремень кожаный чёрный", sku: "MB-AC-250", category: "Аксессуары", price: 3500, store: "На Бауманской", stock: 15 },
  { id: "p12", name: "Блейзер шерсть синий", sku: "MB-BL-040", category: "Блейзеры", price: 22900, store: "На Пятницкой", stock: 5 },
];

// Прайс 09.2026: было 6500, стало 7500. Дублирует @kassa/shared для mock-режима.
export const RENTAL_SERVICE_PRICE = 7500;

// ── Сертификаты (реестр с балансом) ─────────────────────────────────

export const CERTIFICATES: Certificate[] = [
  { number: "45578", nominal: 25000, balance: 10000, status: "active", type: "plastic", guestName: "Андрей", buyerDealNumber: 1042, issuedAt: "2026-03-14", validUntil: "2027-03-14" },
  { number: "45601", nominal: 15000, balance: 0, status: "used", type: "digital", guestName: "Ольга (мужу)", buyerDealNumber: 1051, issuedAt: "2026-02-02", validUntil: "2027-02-02" },
  { number: "45620", nominal: 30000, balance: 30000, status: "active", type: "plastic", guestName: "Виктор", buyerDealNumber: 1066, issuedAt: "2026-05-20", validUntil: "2027-05-20" },
  { number: "45499", nominal: 10000, balance: 10000, status: "expired", type: "plastic", guestName: "Дмитрий", buyerDealNumber: 1003, issuedAt: "2025-04-10", validUntil: "2026-04-10" },
];

// ── Очередь Эдвина (сдача/возврат «не выдано») ─────────────────────

export const QUEUE: QueueItem[] = [
  { id: "q1", kind: "change", dealNumber: 1072, client: "Сергей П.", amount: 1800, destination: "+7 925 110-22-33 · Сбербанк", status: "pending", issuedAmount: 0, payouts: [], createdAt: "2026-06-02T11:20:00" },
  { id: "q2", kind: "refund", dealNumber: 1069, client: "Игорь В.", amount: 28900, destination: "2202 20** **** 4417 · Тинькофф", status: "pending", issuedAmount: 0, payouts: [], createdAt: "2026-06-02T10:05:00" },
  { id: "q3", kind: "change", dealNumber: 1070, client: "Артём Л.", amount: 600, destination: "+7 916 444-55-66 · Альфа", status: "pending", issuedAmount: 0, payouts: [], createdAt: "2026-06-02T09:40:00" },
  { id: "q4", kind: "change", dealNumber: 1065, client: "Павел Н.", amount: 1200, destination: "Наличные на стойке", status: "issued", issuedAmount: 1200, payouts: [], createdAt: "2026-06-01T18:30:00" },
  { id: "q5", kind: "refund", dealNumber: 1061, client: "Марк З.", amount: 4500, destination: "+7 903 222-11-00 · Сбербанк", status: "issued", issuedAmount: 4500, payouts: [], createdAt: "2026-06-01T17:10:00" },
];

// ── Сары (реферальные выплаты за совет) ──────────────────────────────

export const SARY: SaryPayout[] = [
  { id: "s1", client: "Олег Романов", phone: "+7 925 700-11-22", amount: 2000, reason: "посоветовал Сергея П. (заявка #1072)", refDealNumber: 1072, seq: 1, phoneFound: true, status: "pending", screenshotAttached: false, createdAt: "2026-06-02T12:00:00" },
  { id: "s2", client: "Дмитрий К.", phone: "+7 916 330-44-55", amount: 1500, reason: "посоветовал Артёма Л.", refDealNumber: 1070, seq: 1, phoneFound: false, status: "pending", screenshotAttached: false, createdAt: "2026-06-02T10:50:00" },
  { id: "s3", client: "Анна С.", phone: "+7 903 880-77-66", amount: 2000, reason: "посоветовала Павла Н.", refDealNumber: 1065, seq: 1, phoneFound: true, status: "sent", screenshotAttached: true, createdAt: "2026-06-01T19:00:00" },
];

// ── Заявки (примеры под список/доску) ───────────────────────────────

export const DEALS: Deal[] = [
  {
    id: "d1", number: 1072, createdAt: "2026-06-02T11:10:00", funnel: "offline", kind: "sale",
    consultant: "Матвей", clientName: "Сергей П.", clientPhone: "+7 925 110-22-33", store: "На Бауманской",
    channel: "Instagram", purpose: "Свадьба",
    items: [{ productId: "p1", name: "Костюм-тройка тёмно-синий", price: 34900, qty: 1 }, { productId: "p5", name: "Сорочка белая приталенная", price: 4500, qty: 1 }],
    payments: [{ id: "pm1", methodId: "cash_stores", amount: 20000 }, { id: "pm2", methodId: "sber_zhenya", amount: 17600 }],
    stage: "Успех", total: 39400, paid: 39400,
    history: [
      { at: "2026-06-02T11:10:00", who: "Матвей", action: "Заявка создана" },
      { at: "2026-06-02T11:15:00", who: "Матвей", action: "Комментарий: клиент на свадьбу, нужна тройка" },
      { at: "2026-06-02T11:40:00", who: "Матвей", action: "Этап изменён: Новая заявка → Успех" },
    ],
  },
  {
    id: "d2", number: 1071, createdAt: "2026-06-02T10:30:00", funnel: "offline", kind: "rental",
    consultant: "Женя", clientName: "Никита Р.", clientPhone: "+7 916 700-80-90", store: "На Пятницкой",
    channel: "Сарафан", purpose: "Мероприятия",
    items: [{ productId: "rent", name: "Аренда комплекта", price: 6500, qty: 1 }, { productId: "p3", name: "Смокинг чёрный атлас", price: 0, qty: 1, noPrice: true }],
    payments: [{ id: "pm3", methodId: "cash_stores", amount: 6500 }],
    stage: "В аренде", rentalFrom: "2026-06-06", rentalTo: "2026-06-09", total: 6500, paid: 6500,
  },
  {
    id: "d3", number: 1070, createdAt: "2026-06-02T09:35:00", funnel: "offline", kind: "deferred",
    consultant: "Гриша", clientName: "Артём Л.", clientPhone: "+7 916 444-55-66", store: "На Бауманской",
    channel: "Авито", purpose: "Работа",
    items: [{ productId: "p2", name: "Костюм-двойка графит", price: 28900, qty: 1 }],
    payments: [{ id: "pm4", methodId: "sber_zhenya", amount: 10000 }],
    stage: "Товар отложен", reservedUntil: "2026-06-09", total: 28900, paid: 10000,
    history: [
      { at: "2026-06-02T09:35:00", who: "Гриша", action: "Заявка создана" },
      { at: "2026-06-02T09:36:00", who: "Гриша", action: "Внесён аванс 10 000 ₽" },
      { at: "2026-06-02T09:37:00", who: "Гриша", action: "Этап изменён: Новая заявка → Товар отложен" },
    ],
  },
  {
    id: "d4", number: 1069, createdAt: "2026-06-02T09:00:00", funnel: "return", kind: "refund",
    consultant: "Саша", clientName: "Игорь В.", clientPhone: "+7 903 555-66-77", store: "На Пятницкой",
    items: [{ productId: "p2", name: "Костюм-двойка графит", price: 28900, qty: 1 }],
    payments: [], stage: "Провал", total: -28900, paid: 0,
  },
  {
    id: "d5", number: 1068, createdAt: "2026-06-02T08:50:00", funnel: "offline", kind: "cert_plastic",
    consultant: "Арсен", clientName: "Виктор С.", clientPhone: "+7 905 123-45-67", store: "На Бауманской",
    channel: "WhatsApp", guestName: "Виктор",
    items: [{ productId: "cert", name: "Сертификат (номинал)", price: 30000, qty: 1 }],
    payments: [{ id: "pm5", methodId: "rs_pyatnitskaya", amount: 30000 }],
    stage: "Сертификат оплачен", certificateNumber: "45620", total: 30000, paid: 30000,
  },
  {
    id: "d6", number: 1067, createdAt: "2026-06-01T19:20:00", funnel: "offline", kind: "promise",
    consultant: "Матвей", clientName: "Олег Т.", clientPhone: "+7 999 321-00-11", store: "На Бауманской",
    channel: "2ГИС", purpose: "Свадьба",
    items: [{ productId: "p4", name: "Двубортный костюм синий", price: 38900, qty: 1 }],
    payments: [], stage: "Дано обещание", total: 0, paid: 0,
  },
  {
    id: "d7", number: 1066, createdAt: "2026-06-03T10:05:00", funnel: "offline", kind: "sliv",
    consultant: "Гриша", clientName: "Павел К.", clientPhone: "+7 916 111-22-33", store: "На Пятницкой",
    channel: "Instagram", purpose: "Свадьба",
    items: [{ productId: "p1", name: "Костюм-тройка тёмно-синий", price: 34900, qty: 1 }],
    payments: [], stage: "Встреча назначена", total: 34900, paid: 0,
  },
  {
    id: "d8", number: 1065, createdAt: "2026-06-03T09:40:00", funnel: "offline", kind: "sale",
    consultant: "Женя", clientName: "Дмитрий М.", clientPhone: "+7 925 888-77-66", store: "На Бауманской",
    channel: "Сайт", purpose: "Работа", referredBy: "Гриша", meetingDate: "2026-06-01",
    items: [{ productId: "p2", name: "Костюм-двойка графит", price: 28900, qty: 1 }],
    payments: [{ id: "pm7", methodId: "cash_stores", amount: 28900 }],
    stage: "Успех", comment: "Пришёл по сливу из Пятницкой, забрал сразу", total: 28900, paid: 28900,
  },
  {
    id: "d11", number: 1062, createdAt: "2026-06-03T08:30:00", funnel: "offline", kind: "company",
    consultant: "Матвей", clientName: "Анна (бухгалтер)", clientPhone: "+7 495 220-11-00", store: "На Бауманской",
    channel: "Сайт", purpose: "Мероприятия",
    companyName: "ООО «Вектор» · ИНН 7701234567", invoiceNo: "СЧ-2026-114", invoicePaid: true, issued: false,
    items: [{ productId: "p1", name: "Костюм-тройка тёмно-синий", price: 34900, qty: 3 }],
    payments: [{ id: "pm8", methodId: "rs_baumanskaya", amount: 104700 }],
    stage: "Успех", comment: "Корпоративный заказ 3 костюма, счёт оплачен, ждём выдачу", total: 104700, paid: 104700,
  },
  {
    id: "d9", number: 1064, createdAt: "2026-06-03T09:15:00", funnel: "online", kind: "delivery",
    consultant: "Call-менеджер", clientName: "Алексей Н.", clientPhone: "+7 903 444-55-66", store: "Онлайн-магазин",
    channel: "Сайт", purpose: "Свадьба",
    items: [{ productId: "p4", name: "Двубортный костюм синий", price: 38900, qty: 1 }],
    payments: [{ id: "pm6", methodId: "sber_zhenya", amount: 38900 }],
    stage: "Отправлен", total: 38900, paid: 38900,
  },
  {
    id: "d10", number: 1063, createdAt: "2026-06-03T08:50:00", funnel: "offline", kind: "no_sliv",
    consultant: "Саша", clientName: "Роман В.", clientPhone: "+7 916 333-22-11", store: "На Пятницкой",
    channel: "2ГИС", purpose: "Мероприятия",
    items: [],
    payments: [], stage: "Новая заявка", total: 0, paid: 0,
  },
  // Демо: обещание — для проверки кнопки «Обновить по телефону» в сливе
  // Телефон клиента совпадает с d13 (Продажа), нажмите «Обновить» в блоке слив
  {
    id: "d12", number: 1060, createdAt: "2026-06-01T14:00:00", funnel: "offline", kind: "promise",
    consultant: "Женя", clientName: "Борис К.", clientPhone: "+7 925 777-88-99", store: "На Пятницкой",
    channel: "Instagram", purpose: "Свадьба",
    items: [{ productId: "p4", name: "Двубортный костюм синий", price: 38900, qty: 1 }],
    payments: [], stage: "Дано обещание", total: 38900, paid: 0,
  },
  // Демо: продажа с тем же телефоном (Борис К.) — будет найдена кнопкой «Обновить»
  {
    id: "d13", number: 1059, createdAt: "2026-06-04T11:30:00", funnel: "offline", kind: "sale",
    consultant: "Матвей", clientName: "Борис К.", clientPhone: "+7 925 777-88-99", store: "На Бауманской",
    channel: "Instagram", purpose: "Свадьба",
    referredBy: "Женя", meetingDate: "2026-06-01",
    items: [{ productId: "p4", name: "Двубортный костюм синий", price: 38900, qty: 1 }],
    payments: [{ id: "pm9", methodId: "sber_matvey", amount: 38900 }],
    stage: "Успех", comment: "Пришёл по обещанию Жени с Пятницкой", total: 38900, paid: 38900,
  },
  // Демо: компания с руководителем (новая заявка для CompanyForm)
  {
    id: "d14", number: 1058, createdAt: "2026-06-04T10:00:00", funnel: "offline", kind: "company",
    consultant: "Гриша", clientName: "Мария (бухгалтер)", clientPhone: "+7 495 310-44-55", store: "На Бауманской",
    channel: "Сайт", purpose: "Мероприятия",
    companyName: "ИП Петров А.В.",
    managerName: "Петров Алексей Викторович",
    managerPhone: "+7 903 600-00-01",
    deferredUntil: "2026-07-04",
    items: [{ productId: "p1", name: "Костюм-тройка тёмно-синий", price: 34900, qty: 2 }],
    payments: [],
    stage: "Товар отложен", comment: "Корпоратив 4 июля, нужно 2 костюма-тройки", total: 69800, paid: 0,
  },
  // Демо: электронный сертификат (онлайн)
  {
    id: "d15", number: 1057, createdAt: "2026-06-04T12:10:00", funnel: "online", kind: "cert_digital",
    consultant: "Матвей", callManager: "Сергей (call)", clientName: "Ирина Л.", clientPhone: "+7 916 200-30-40",
    email: "irina.l@example.com", store: "Онлайн-магазин",
    channel: "Instagram", guestName: "Муж Ирины",
    items: [{ productId: "cert", name: "Сертификат №45640", price: 20000, qty: 1 }],
    payments: [{ id: "pm15", methodId: "sber_zhenya", amount: 20000 }],
    stage: "Сертификат оплачен", certificateNumber: "45640", receivedAt: "2026-06-04", validUntil: "2027-06-04",
    total: 20000, paid: 20000,
  },
  // Демо: дефект (брак, без клиента)
  {
    id: "d16", number: 1056, createdAt: "2026-06-04T13:00:00", funnel: "offline", kind: "defect",
    consultant: "Гриша", clientName: "—", clientPhone: "—", store: "На Пятницкой",
    items: [{ productId: "p3", name: "Смокинг чёрный атлас", price: 0, qty: 1, noPrice: true }],
    payments: [], stage: "Взято в работу", photoAttached: true,
    comment: "Пятно на лацкане, отправить на склад брака", total: 0, paid: 0,
  },
  // Демо: химчистка
  {
    id: "d17", number: 1055, createdAt: "2026-06-04T13:20:00", funnel: "offline", kind: "drycleaning",
    consultant: "Саша", clientName: "—", clientPhone: "—", store: "На Бауманской",
    items: [{ productId: "p1", name: "Костюм-тройка тёмно-синий", price: 0, qty: 1, noPrice: true }],
    payments: [], stage: "Взято в работу", photoAttached: true,
    comment: "После аренды — в химчистку", total: 0, paid: 0,
  },
  // Демо: обмен (возврат + новые позиции)
  {
    id: "d18", number: 1054, createdAt: "2026-06-04T14:00:00", funnel: "return", kind: "exchange",
    consultant: "Женя", clientName: "Кирилл П.", clientPhone: "+7 925 410-50-60", store: "На Бауманской",
    linkedDealNumber: 1072,
    items: [
      { productId: "p2", name: "Костюм-двойка графит", price: -28900, qty: 1 },
      { productId: "p1", name: "Костюм-тройка тёмно-синий", price: 34900, qty: 1 },
    ],
    payments: [{ id: "pm18", methodId: "cash_stores", amount: 6000 }],
    stage: "Успех", comment: "Обмен двойки на тройку, доплата 6000", total: 6000, paid: 6000,
  },
];
