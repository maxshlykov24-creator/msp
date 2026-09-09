import type {
  PaymentKind,
  PaymentMethod,
  Payout,
  QueueKind,
  Store,
  SuitLine,
  SuitPart,
} from "./types.js";

// Справочники, общие для фронта и бэка. Реальные значения из amoCRM / МойСклад / ТЗ.

export const STORES = ["На Бауманской", "На Пятницкой", "Онлайн-магазин"] as const;

export const STORE_ADDRESS: Record<Store, string> = {
  "На Бауманской": "На Бауманской",
  "На Пятницкой": "На Пятницкой",
  "Онлайн-магазин": "Онлайн-магазин",
};

// Маппинг шоурум → название склада МойСклад (источник отгрузки).
// NB: реальный склад для шоурума «На Пятницкой» в МойСклад называется
// «На Новокузнецкой» (подтверждено синком каталога 2026-07-01) — имена
// showroom'а и склада МойСклад для этой точки исторически разошлись.
export const STORE_TO_WAREHOUSE: Record<Store, string | null> = {
  "На Бауманской": "На Бауманской",
  "На Пятницкой": "На Новокузнецкой",
  "Онлайн-магазин": null,
};

/**
 * Расположение товара (созвон 29.07, п.13). Порядок важен — так его видит консультант.
 * Первые семь — реальные склады МойСклад, «В пути» и «СДЭК» — статусы позиции
 * в заявке (в МойСклад складов «СДЭК» нет). Два значения СДЭК сведены в одно
 * на созвоне 20.08 («СДЭК можно сделать общим»).
 */
export const ITEM_LOCATIONS = [
  "На Новокузнецкой",
  "На Бауманской",
  "Центральный склад",
  "Полупарки на Новокузнецкой",
  "Полупарки на Бауманской",
  "Ателье",
  "В пути",
  "СДЭК",
] as const;

export type ItemLocation = (typeof ITEM_LOCATIONS)[number];

/** Единый статус СДЭК; старые значения «СДЭК у клиента» / «СДЭК у нас» сведены к нему. */
export const CDEK_LOCATION: ItemLocation = "СДЭК";

/**
 * Идентификатор строки СДЭК в остатках товара. Склада с таким именем в МойСклад
 * нет, количество считает касса по расположению позиций в заявках, поэтому строка
 * помечена как виртуальная и не входит в сумму доступного остатка.
 */
export const CDEK_WAREHOUSE_ID = "virtual:cdek";

export function isVirtualWarehouse(warehouseMsId: string | undefined | null): boolean {
  return (warehouseMsId ?? "").startsWith("virtual:");
}

/** Нормализация исторических значений расположения к текущему справочнику. */
export function normalizeItemLocation(location: string | undefined | null): string {
  const value = (location ?? "").trim();
  if (!value) return "";
  if (/^сдэк/i.test(value)) return CDEK_LOCATION;
  return value;
}

/** Статусы позиции в заявке — вытесняют этапы «Ждет товар» / «Товар в магазине» на сделке. */
export const ITEM_STATUSES = [
  { id: "waiting", label: "Ждет товар" },
  { id: "in_store", label: "Товар в магазине" },
  { id: "booked", label: "Забронирован" },
  { id: "reserved", label: "Товар отложен" },
] as const;

export type ItemStatusId = (typeof ITEM_STATUSES)[number]["id"];

export function itemStatusLabel(id?: string | null): string {
  return ITEM_STATUSES.find((s) => s.id === id)?.label ?? "—";
}

/** Склады МойСклад, по которым синкаем остатки (без СДЭК-статусов). */
export const STOCK_WAREHOUSES: string[] = ITEM_LOCATIONS.filter(
  (name) => !name.startsWith("СДЭК")
);

/**
 * Физический склад полупарков. Такой остаток уже признан некомплектом руками,
 * его не смешиваем с вычисленным: иначе одно и то же изделие попадёт дважды.
 */
export function isHalfSetWarehouse(name: string | undefined | null): boolean {
  return /полупарк/i.test((name ?? "").trim());
}

/** Куда можно переместить товар под заявку. */
export const MOVEMENT_TARGETS: string[] = ["На Новокузнецкой", "На Бауманской"];

/** Перемещение с центрального склада ведёт логист, остальное — консультант магазина-источника. */
export const CENTRAL_WAREHOUSE = "Центральный склад";

/**
 * Ключ склада для сопоставления UI ↔ МойСклад.
 * В МС центральный склад часто называется просто «Центральный», а в кассе —
 * «Центральный склад»; шоурумы — «На Бауманской» / «Бауманская».
 */
export function warehouseMatchKey(name: string): string | null {
  const n = name.toLowerCase().trim();
  if (!n) return null;
  if (/полупарк/.test(n) && /новокузнецк/.test(n)) return "half_novo";
  if (/полупарк/.test(n) && /бауманск/.test(n)) return "half_bauman";
  if (/новокузнецк/.test(n)) return "novo";
  if (/бауманск/.test(n)) return "bauman";
  if (/центральн/.test(n)) return "central";
  if (/ателье/.test(n)) return "atelier";
  if (/в пути/.test(n)) return "transit";
  return null;
}

/**
 * Порядок складов в окне остатков (как в ITEM_LOCATIONS / запрос владельца):
 * Новокузнецкая → Бауманская → Центральный → полупарки → Ателье → В пути.
 */
const WAREHOUSE_DISPLAY_ORDER = [
  "novo",
  "bauman",
  "central",
  "half_novo",
  "half_bauman",
  "atelier",
  "transit",
] as const;

export function warehouseDisplayIndex(name: string): number {
  const key = warehouseMatchKey(name);
  if (!key) return 999;
  const i = (WAREHOUSE_DISPLAY_ORDER as readonly string[]).indexOf(key);
  return i < 0 ? 999 : i;
}

/** Сортировка строк остатков для модалки «Остатки» и списка в каталоге. */
export function sortWarehousesByDisplayOrder<T extends { name: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const di = warehouseDisplayIndex(a.name) - warehouseDisplayIndex(b.name);
    if (di !== 0) return di;
    return a.name.localeCompare(b.name, "ru");
  });
}

/** Центральный склад МойСклад (в т.ч. короткое имя «Центральный»). */
export function isCentralWarehouse(name: string | undefined | null): boolean {
  return warehouseMatchKey(name ?? "") === "central";
}

/**
 * Находит id склада МойСклад по имени из UI/справочника.
 * Сначала по ключу (центральн / бауманск…), затем по нормализованному includes.
 */
export function findWarehouseId(
  warehouses: Array<{ id: string; name: string }>,
  uiName: string
): string | undefined {
  const key = warehouseMatchKey(uiName);
  if (key) {
    const byKey = warehouses.find((row) => warehouseMatchKey(row.name) === key);
    if (byKey) return byKey.id;
  }
  const needle = uiName
    .toLowerCase()
    .trim()
    .replace(/^на\s+/, "")
    .replace(/\s+склад\b/g, "")
    .replace(/\s+/g, " ");
  if (!needle) return undefined;
  const hit = warehouses.find((row) => {
    const hay = row.name
      .toLowerCase()
      .trim()
      .replace(/^на\s+/, "")
      .replace(/\s+склад\b/g, "")
      .replace(/\s+/g, " ");
    return hay === needle || hay.includes(needle) || needle.includes(hay);
  });
  return hit?.id;
}

/**
 * Имя склада МС → значение из ITEM_LOCATIONS (для статуса позиции в заявке).
 * «Центральный» → «Центральный склад».
 */
export function itemLocationFromMsName(storeName: string | undefined | null): string {
  if (!storeName) return "";
  const trimmed = storeName.trim();
  const exact = ITEM_LOCATIONS.find((loc) => loc.toLowerCase() === trimmed.toLowerCase());
  if (exact) return exact;
  const key = warehouseMatchKey(trimmed);
  if (key) {
    const byKey = ITEM_LOCATIONS.find((loc) => warehouseMatchKey(loc) === key);
    if (byKey) return byKey;
  }
  return trimmed;
}

/**
 * Склад назначения → шоурум/очередь принимающей стороны.
 * «На Новокузнецкой» в МС = шоурум «На Пятницкой».
 */
export function storeForAcceptQueue(warehouseName: string | undefined | null): string {
  if (!warehouseName) return "";
  const key = warehouseMatchKey(warehouseName);
  if (key === "central") return CENTRAL_WAREHOUSE;
  for (const store of STORES) {
    const wh = STORE_TO_WAREHOUSE[store];
    if (wh && warehouseMatchKey(wh) === key) return store;
  }
  if (key === "novo") return "На Пятницкой";
  if (key === "bauman") return "На Бауманской";
  return itemLocationFromMsName(warehouseName) || warehouseName;
}

// Организация-продавец в МойСклад (резолвится в meta при старте).
export const MS_ORGANIZATION_NAME = "MANSBAND";

// Контрагент для анонимной розницы (продажа без телефона клиента).
export const MS_RETAIL_COUNTERPARTY_NAME = "Розничный покупатель";

// Воронки amoCRM (pipeline_id) — из _private/kassa_access.env.
export const AMO_PIPELINE_SALES = 9601214; // Продажи
export const AMO_PIPELINE_COMPLAINTS = 9601246; // Жалобы / возвраты / обмены

export const RENTAL_SERVICE_PRICE = 6500;

export const AD_SOURCES = [
  "Совет",
  "Сарафан",
  "Телеграм канал",
  "Instagram наш",
  "Instagram не наш",
  "ВК",
  "Яндекс.Директ.Поиск",
  "Яндекс.Директ.РСЯ",
  "Яндекс Карты",
  "2ГИС",
  "Гугл Карты",
  "Авито",
  "Ютуб",
  "TikTok",
  "Pinterest",
  "Промокод Чат невест",
  "Промокод Osoba",
  "Промокод Онливед",
] as const;

/**
 * Способы приёма и выдачи денег (созвон 29.07.2026). Единый источник для формы
 * оплаты, очереди Эдвина и разнесения платежей в МойСклад: `kind` берётся
 * отсюда, а не выводится эвристикой по строке id.
 */
export const PAYMENT_METHODS: PaymentMethod[] = [
  { id: "cash_zhenya", label: "Наличные Женя", kind: "cash" },
  { id: "cash_matvey", label: "Наличные Матвей", kind: "cash" },
  { id: "cash_sasha", label: "Наличные Саша", kind: "cash" },
  { id: "cash_grisha", label: "Наличные Гриша", kind: "cash" },
  { id: "cash_arsen", label: "Наличные Арсен", kind: "cash" },
  { id: "cash_nina", label: "Наличные Нина", kind: "cash" },

  { id: "sber_misha", label: "Сбер Миша", kind: "card" },
  { id: "sber_mziya", label: "Сбер Мзия", kind: "card" },
  { id: "sber_zhenya", label: "Сбер Женя", kind: "card" },
  { id: "sber_matvey", label: "Сбер Матвей", kind: "card" },
  { id: "sber_grisha", label: "Сбер Гриша", kind: "card" },
  { id: "alfa_edwin", label: "Альфа Эдвин", kind: "card" },
  { id: "alfa_zhenya", label: "Альфа Женя", kind: "card" },
  { id: "alfa_grisha", label: "Альфа Гриша", kind: "card" },
  { id: "vtb_grisha", label: "ВТБ Гриша", kind: "card" },
  { id: "vtb_zhenya", label: "ВТБ Женя", kind: "card" },
  { id: "tinkoff_grisha", label: "Тинькофф Гриша", kind: "card" },

  { id: "rs_pyatnitskaya", label: "РС Пятницкая", kind: "account" },
  { id: "rs_baumanskaya", label: "РС Бауманская", kind: "account" },

  { id: "cert", label: "Сертификат №…", kind: "certificate" },
];

/** Порядок и подписи групп в селекте способов оплаты. */
export const PAYMENT_METHOD_GROUPS: Array<{ kind: PaymentKind; label: string }> = [
  { kind: "cash", label: "Наличные" },
  { kind: "card", label: "Безналичные" },
  { kind: "account", label: "РС" },
  { kind: "certificate", label: "Сертификат" },
];

/**
 * Устаревшие id способов оплаты из ранних версий кассы. Нужны, чтобы старые
 * заявки и записи ledger по-прежнему определяли kind и показывали подпись.
 */
const LEGACY_PAYMENT_METHOD_IDS: Record<string, string> = {
  cash: "cash_zhenya",
  cash_zh: "cash_zhenya",
  cash_mt: "cash_matvey",
  sber_zh: "sber_zhenya",
  sber_mt: "sber_matvey",
  sber_afina: "sber_mziya",
  ozon_zh: "alfa_zhenya",
  rs_pyat: "rs_pyatnitskaya",
  rs_misha: "rs_baumanskaya",
};

/** Сняты с выбора, но остаются в истории старых заявок. */
const RETIRED_PAYMENT_METHODS: PaymentMethod[] = [
  { id: "cash_stores", label: "Наличные (Бауманская / Пятницкая)", kind: "cash" },
];

/**
 * Только расход в очереди Эдвина — не в общем селекте оплаты заявки.
 */
export const EDWIN_CASH_METHOD: PaymentMethod = {
  id: "cash_edwin",
  label: "Наличные Эдвина",
  kind: "cash",
};

export function findPaymentMethod(methodId: string): PaymentMethod | undefined {
  const id = methodId.trim();
  const direct = PAYMENT_METHODS.find((m) => m.id === id);
  if (direct) return direct;
  if (id === EDWIN_CASH_METHOD.id) return EDWIN_CASH_METHOD;
  const retired = RETIRED_PAYMENT_METHODS.find((m) => m.id === id);
  if (retired) return retired;
  const mapped = LEGACY_PAYMENT_METHOD_IDS[id];
  if (!mapped) return undefined;
  return (
    PAYMENT_METHODS.find((m) => m.id === mapped) ??
    RETIRED_PAYMENT_METHODS.find((m) => m.id === mapped)
  );
}

/** Тип способа оплаты для разнесения в МойСклад и ledger. */
export function paymentKindOf(methodId: string): PaymentKind {
  const known = findPaymentMethod(methodId);
  if (known) return known.kind;
  // Заявки из amoCRM могут прийти с произвольной строкой — падать нельзя.
  const m = methodId.toLowerCase();
  if (m.startsWith("cert")) return "certificate";
  if (m.startsWith("rs") || m.includes("счет") || m.includes("счёт")) return "account";
  if (m.startsWith("cash") || m.includes("налич")) return "cash";
  return "card";
}

export function paymentMethodLabel(methodId: string): string {
  if (methodId === MANSBAND_PAYOUT_METHOD) return MANSBAND_PAYOUT_LABEL;
  return findPaymentMethod(methodId)?.label ?? methodId;
}

// ── Выплаты: сдача, чаевые, возврат ────────────────────────────────────

/**
 * Особая строка выплаты: деньги переводит Mansband, а не консультант из кассы.
 * Такая строка не трогает кассу магазина, а создаёт позицию в очереди Эдвина.
 */
export const MANSBAND_PAYOUT_METHOD = "mansband";
export const MANSBAND_PAYOUT_LABEL = "Перевести Mansband";

/**
 * Дефолт только для выдачи в очереди Эдвина (не для расхода — там «— выбрать —»).
 * В форме заявки консультант обязан выбрать способ сам.
 */
export const DEFAULT_PAYOUT_METHOD_ID = "cash_zhenya";

/**
 * Приводит раскладку выплаты к сумме `total`: последняя строка всегда добирает
 * остаток, поэтому сумма строк равна выплате и «повисших» денег не бывает.
 * Пустой список превращается в одну строку на всю сумму (methodId пустой —
 * консультант должен выбрать).
 */
export function normalizePayouts(
  rows: Payout[] | undefined,
  total: number,
  fallbackMethodId = ""
): Payout[] {
  const target = Math.max(0, Math.round(total));
  if (target === 0) return [];
  const base = rows && rows.length > 0 ? rows : [{ methodId: fallbackMethodId, amount: target }];
  const out: Payout[] = [];
  let used = 0;
  for (const row of base.slice(0, -1)) {
    const amount = Math.max(0, Math.min(Math.round(row.amount) || 0, target - used));
    used += amount;
    out.push({ ...row, amount });
  }
  const last = base[base.length - 1]!;
  out.push({ ...last, amount: Math.max(0, target - used) });
  return out;
}

/** Сколько из выплаты уходит в очередь Эдвина («Перевести Mansband»). */
export function mansbandPayoutAmount(rows: Payout[] | undefined): number {
  return (rows ?? [])
    .filter((row) => row.methodId === MANSBAND_PAYOUT_METHOD)
    .reduce((sum, row) => sum + row.amount, 0);
}

/** Строки, которые консультант выдал сам (наличные, перевод со своей карты). */
export function selfPayouts(rows: Payout[] | undefined): Payout[] {
  return (rows ?? []).filter(
    (row) => row.methodId !== MANSBAND_PAYOUT_METHOD && row.amount > 0
  );
}

/**
 * Сарафан (правки владельца 10.08.2026, п.3): клиенту — скидка в чеке,
 * другу — такая же выплата в очереди колл-менеджера.
 */
export const SARY_BONUS = 1000;

/**
 * САР начисляется при чеке от этой суммы, если костюмов в чеке нет
 * (созвон 20.08: «чек на 20к+»). Рабочее значение хранится в app_settings
 * (ключ saryMinCheck) и правится из настроек rop/admin; эта константа —
 * дефолт при пустой настройке.
 */
export const SARY_MIN_CHECK_DEFAULT = 20000;

/**
 * Группы МойСклад, которые считаются костюмом при начислении САР (созвон 04.09:
 * «сар должно прилетать столько, сколько было костюмов в чеке»). Сравнение идёт
 * по началу пути группы товара, поэтому «Костюмы» покрывает и «Костюмы/Тройки».
 * Рабочий список — в app_settings (ключ sarySuitGroups), правится из настроек
 * без релиза; здесь дефолт до уточнения точного состава веток у владельца.
 */
export const SARY_SUIT_GROUPS_DEFAULT = ["Костюмы"];

/** Путь группы товара МойСклад относится к костюмам (сравнение по префиксу). */
export function isSuitCategory(category: string | undefined | null, groups: string[]): boolean {
  const path = (category ?? "").trim().toLowerCase();
  if (!path) return false;
  return groups.some((group) => {
    const needle = group.trim().toLowerCase();
    if (!needle) return false;
    return path === needle || path.startsWith(`${needle}/`);
  });
}

// ── Костюм как единица учёта ────────────────────────────────────────
// В МойСкладе костюма нет: заведены виды (пиджак, брюки, жилет) с модификациями.
// Части одного костюма связывает характеристика «Вариация» — она одинакова у
// пиджака, брюк и жилета одной модели. Комплекты МС (bundle) не используем:
// коды маркировки живут на изделиях, а полупарк комплектом не описать.

/** Состав костюма по типам комплектов из пайплайна поставок (KIT_MAP). */
export const SUIT_PART_KEYWORDS_DEFAULT: Record<SuitPart, string[]> = {
  jacket: ["пиджак", "блейзер"],
  trousers: ["брюки", "слаксы"],
  vest: ["жилет"],
};

/** Костюм без жилета — двойка, с жилетом — тройка. */
export const SUIT_PART_LABEL: Record<SuitPart, string> = {
  jacket: "пиджак",
  trousers: "брюки",
  vest: "жилет",
};

/**
 * Допустимое расхождение размеров верха и низа, при котором костюм всё ещё
 * считается продаваемым («правильный полупарк»). На цифры влияет слабо: при
 * допуске 2 цельных костюмов становится 15 112 против 14 971 при строгом
 * совпадении. Рабочее значение — в app_settings (ключ suitSizeTolerance).
 */
export const SUIT_SIZE_TOLERANCE_DEFAULT = 2;

/**
 * Возраст партии для светофора склада. Нормативы называет владелец, здесь
 * дефолт до его цифр: полгода жёлтый, год красный.
 */
export const STOCK_AGE_YELLOW_DAYS_DEFAULT = 180;
export const STOCK_AGE_RED_DAYS_DEFAULT = 365;

/** Часть костюма по названию вида МойСклад. Не костюмный вид — null. */
export function suitPartOf(
  productName: string | undefined | null,
  keywords: Record<SuitPart, string[]> = SUIT_PART_KEYWORDS_DEFAULT
): SuitPart | null {
  // У модификации имя вида идёт до скобки с характеристиками.
  const name = (productName ?? "").split(" (")[0]!.trim().toLowerCase();
  if (!name) return null;
  for (const part of ["jacket", "trousers", "vest"] as SuitPart[]) {
    if ((keywords[part] ?? []).some((word) => name.includes(word.trim().toLowerCase()))) return part;
  }
  return null;
}

/** Линия костюма по названию вида: смокинговые виды не смешиваются с обычными. */
export function suitLineOf(productName: string | undefined | null): SuitLine {
  return (productName ?? "").toLowerCase().includes("смокинг") ? "smoking" : "regular";
}

/** Ключ модели костюма: вариация плюс всё, что делает её одной моделью. */
export interface SuitModelKey {
  variation: string;
  color: string;
  pattern: string;
  fit: string;
  height: string;
  line: SuitLine;
}

export function suitModelId(key: SuitModelKey): string {
  return [key.variation, key.color, key.pattern, key.fit, key.height, key.line]
    .map((part) => (part ?? "").trim().toLowerCase())
    .join("|");
}

/**
 * Человеческое название костюма для экрана остатков: «Костюм тройка чёрный
 * однотонный slim fit». Цвет и узор берём по пиджаку — внутри вариации они
 * совпадают не всегда (цвет только у 70% моделей), крой совпадает у 98%.
 */
export function suitTitle(input: {
  hasVest: boolean;
  line: SuitLine;
  color?: string | null;
  pattern?: string | null;
  fit?: string | null;
}): string {
  const kind = input.line === "smoking" ? "смокинг" : "костюм";
  const parts = [
    kind,
    input.hasVest ? "тройка" : "двойка",
    (input.color ?? "").trim(),
    (input.pattern ?? "").trim(),
    (input.fit ?? "").trim(),
  ].filter(Boolean);
  const title = parts.join(" ");
  return title.charAt(0).toUpperCase() + title.slice(1);
}

/**
 * Размер как число: в МС он строкой, а сравнивать нужно по величине, чтобы
 * считать допуск. Нечисловой размер даёт null — такую позицию не парим.
 */
export function suitSizeNumber(size: string | undefined | null): number | null {
  const raw = (size ?? "").trim().replace(",", ".");
  if (!raw) return null;
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * Дефолт настроек мотивации (созвон 20.08): % от выручки за день против
 * обеспечительной ставки 5000 ₽/день; разделы конверсии — как продиктовал Миша,
 * суммы премий владелец задаёт в конструкторе (дефолт 0 — премия не начисляется).
 */
export const DEFAULT_PAYROLL_SETTINGS = {
  revenuePct: 5,
  dailyFloor: 5000,
  conversionTiers: [
    { from: 85, to: 87.001, bonusPct: 0 },
    { from: 87.001, to: 90.001, bonusPct: 0 },
    { from: 90.001, to: 93, bonusPct: 0 },
    { from: 93, to: null, bonusPct: 0 },
  ],
  uptTiers: [] as Array<{ from: number; to: number | null; bonusPct: number }>,
  penaltyConversionTiers: [] as Array<{ from: number; to: number | null; bonusPct: number }>,
  penaltyUptTiers: [] as Array<{ from: number; to: number | null; bonusPct: number }>,
} as const;

/** Закрытый справочник Эдвина. Меняется только отдельной миграцией/релизом. */
export const EDWIN_EXPENSE_CATEGORIES = [
  "Возврат",
  "Сдача",
  "Чаевые",
  "Банковское обслуживание",
  "Логистика",
  "Закупка товара",
  "Хоз товары, канцелярия",
  "Зарплата",
  "Оплата подрядчикам",
  "Таргет",
  "Съемки",
  "Онлайн сервисы",
  "Ателье и ремонт изделий",
  "Оснащение магазина",
  "Аренда помещений",
  "Налоги",
  // Отправленные САР: 1000 ₽ за рекомендацию (созвон 20.08). Пишется
  // автоматически из markSaryBatchSent, доступна и для ручного расхода.
  "Программа лояльности",
  "Прочее",
] as const;

/** @deprecated Используйте точное доменное имя EDWIN_EXPENSE_CATEGORIES. */
export const EXPENSE_CATEGORIES = EDWIN_EXPENSE_CATEGORIES;

/**
 * Исторические имена этапов amoCRM. Резолв статуса идёт по имени, поэтому
 * переименование в CRM и релиз кода не обязаны совпасть по времени.
 */
export const STAGE_ALIASES: Record<string, string[]> = {
  "Сертификат оплачен": ["Сертификат продан"],
  "Счёт запрошен": ["Счет запрошен"],
  "Счет выставлен": ["Счёт выставлен"],
  "Ждет товар": ["Ждёт товар"],
};

/**
 * Системные закрытые этапы amoCRM: id общие для всех воронок. В кэше справочников
 * они схлопываются в одну строку (уникальность по kind+amo_id), поэтому по воронке
 * их не найти — резолвим по константе, иначе Успех/Провал не уезжают в CRM.
 */
export const AMO_SYSTEM_STATUS_IDS: Record<string, number> = {
  успех: 142,
  провал: 143,
};

/**
 * Конвейер продажи компании:
 * консультант заводит на «Товар отложен» → Эдвину сразу задача «Выставить счет»,
 * далее «Счет выставлен» / оплата / документы, консультант выдаёт товар.
 */
export const COMPANY_STAGES = [
  "Товар отложен",
  "Счёт запрошен",
  "Счет выставлен",
  "Оплачено",
  "Ждет товар",
  "Документы готовы",
  "Документы переданы",
  "Успех",
  "Провал",
] as const;

/**
 * Этапы компании, с которых номер и дата счёта обязательны. Консультант заявку
 * заводит без счёта — реквизиты появляются, когда Эдвин выставил счёт, поэтому
 * этап «Счет выставлен» сюда не входит (правки 05.08, п.9).
 */
export const COMPANY_STAGES_WITH_INVOICE: string[] = [
  "Оплачено",
  "Документы готовы",
  "Документы переданы",
  "Товар выдан",
  "Успех",
];

/**
 * Цепочка задач по продаже компании.
 * Эдвин: invoice → invoice_check.
 * Миша: после оплаты счёта сразу documents + documents_hand + atelier (если есть);
 * в UI доступны по порядку.
 */
export const COMPANY_QUEUE_CHAIN: Array<{ kind: QueueKind; label: string; owner: "Эдвин" | "Миша" }> = [
  { kind: "invoice", label: "Выставить счет", owner: "Эдвин" },
  { kind: "invoice_check", label: "Проверить оплату", owner: "Эдвин" },
  { kind: "documents", label: "Подготовить документы", owner: "Миша" },
  { kind: "documents_hand", label: "Передать документы", owner: "Миша" },
  { kind: "atelier", label: "Оплатить ателье", owner: "Миша" },
];

export function companyQueueLabel(kind: string): string {
  return COMPANY_QUEUE_CHAIN.find((step) => step.kind === kind)?.label ?? kind;
}

export const RENTAL_STAGES = [
  "Новая заявка",
  "Зарезервировано",
  "Аренда оплачена",
  "Комплект выдан",
  "Комплект возвращён",
  "Успех",
  "Провал",
] as const;

// Имена кастом-полей сделки (лида) amoCRM для writeback.
// Точные field_id резолвятся по имени при bootstrap-синке (getLeadFieldIds/getFieldIdByName) —
// id НЕ хардкодим, чтобы не ломаться при пересоздании полей в amoCRM.
// Поля, отмеченные (новое) — создаются автоматически при bootstrap, если ещё не существуют
// (см. ensureLeadFieldsExist в apps/api/src/services/bootstrap.ts).
export const AMO_LEAD_FIELDS = {
  consultant: "Отв-ный консультант",
  purpose: "Цель покупки",
  channel: "Канал продаж",
  comment: "Комментарий к заказу",
  storeAddress: "Адрес магазина",
  guestName: "Имя гостя",
  certificateNumber: "№ Сертификата",
  validUntil: "Сертификат действителен до",
  invoiceNo: "№ Счета",
  orderContains: "Состав заказа",
  msOrder: "Заказ МойСклад",
  msDemand: "№ Отгрузки",
  paymentStatus: "Статус оплаты",
  reservedUntil: "Отложка до",
  otlozhkaKind: "Отложка", // select: "Платная" / "Бесплатная"
  deferredUntil: "Резерв до",
  meetingDate: "Дата встречи",
  actualUntil: "Актуально до (обещание)", // новое
  rentalFrom: "Аренда от", // новое
  rentalTo: "Аренда до",
  referredBy: "Направивший консультант", // новое
  callManager: "Консультант", // новое
  deliveryAddress: "Адрес доставки",
  atelierAmount: "Сумма ателье",
  deliveryAmount: "Сумма доставки",
  closingDocumentsRequired: "Нужны закрывающие документы",
  invoiceDate: "Дата счёта",
  invoiceStatus: "Статус счёта",
  rentalStatus: "Статус аренды",
  rentalIssuedAt: "Аренда выдана",
  rentalReturnedAt: "Аренда возвращена",
  rentalDeposit: "Залог аренды",
  sara: "Сара",
  wasRefund: "Был возврат", // checkbox, только API
  wasExchange: "Был обмен", // checkbox, только API
  // Прямая ссылка на заявку в кассе: колл-менеджер ставит задачи из сделки amoCRM,
  // не разыскивая заявку руками (созвон 04.09). Новое поле.
  kassaLink: "Ссылка на кассу",
} as const;

/**
 * Прямая ссылка на карточку заявки в кассе. Роутинг фронта — по hash,
 * `board/<группа>/<статус>/<номер>`; «all/all» открывает карточку из общего списка.
 */
export function dealDeepLink(baseUrl: string, dealNumber: number): string {
  return `${baseUrl.replace(/\/+$/, "")}/#board/all/all/${dealNumber}`;
}

export type AmoLeadFieldKey = keyof typeof AMO_LEAD_FIELDS;

export type AmoNewFieldType = "date" | "text" | "checkbox";

// Поля лида, которые нужно создать в amoCRM при bootstrap, если их ещё нет.
export const AMO_NEW_LEAD_FIELDS: Array<{
  key: AmoLeadFieldKey;
  type: AmoNewFieldType;
  isApiOnly?: boolean;
}> = [
  { key: "actualUntil", type: "date" },
  { key: "rentalFrom", type: "date" },
  { key: "referredBy", type: "text" },
  { key: "callManager", type: "text" },
  { key: "atelierAmount", type: "text" },
  { key: "deliveryAmount", type: "text" },
  { key: "closingDocumentsRequired", type: "checkbox" },
  { key: "invoiceDate", type: "date" },
  { key: "invoiceStatus", type: "text" },
  { key: "rentalStatus", type: "text" },
  { key: "rentalIssuedAt", type: "date" },
  { key: "rentalReturnedAt", type: "date" },
  { key: "rentalDeposit", type: "text" },
  { key: "wasRefund", type: "checkbox", isApiOnly: true },
  { key: "wasExchange", type: "checkbox", isApiOnly: true },
  { key: "kassaLink", type: "text" },
];

// Кастом-поля сущности «Компания» amoCRM.
export const AMO_COMPANY_FIELDS = {
  manager: "Руководитель",
} as const;

export type AmoCompanyFieldKey = keyof typeof AMO_COMPANY_FIELDS;

export const AMO_NEW_COMPANY_FIELDS: Array<{ key: AmoCompanyFieldKey; type: "date" | "text" }> = [
  { key: "manager", type: "text" },
];

/** @deprecated используйте AMO_LEAD_FIELDS — оставлено для обратной совместимости. */
export const AMO_WRITEBACK_FIELDS = {
  msOrder: AMO_LEAD_FIELDS.msOrder,
  msDemand: AMO_LEAD_FIELDS.msDemand,
  paymentStatus: AMO_LEAD_FIELDS.paymentStatus,
} as const;
