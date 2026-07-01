import type { Store } from "./types.js";

// Справочники, общие для фронта и бэка. Реальные значения из amoCRM / МойСклад / ТЗ.

export const STORES = ["На Бауманской", "На Пятницкой", "Онлайн-магазин"] as const;

export const STORE_ADDRESS: Record<Store, string> = {
  "На Бауманской": "г. Москва, Спартаковская пл., д. 14, стр. 2",
  "На Пятницкой": "г. Москва, ул. Пятницкая, д. 8",
  "Онлайн-магазин": "Онлайн · отправка",
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

// Организация-продавец в МойСклад (резолвится в meta при старте).
export const MS_ORGANIZATION_NAME = "MANSBAND";

// Контрагент для анонимной розницы (продажа без телефона клиента).
export const MS_RETAIL_COUNTERPARTY_NAME = "Розничный покупатель";

// Воронки amoCRM (pipeline_id) — из _private/kassa_access.env.
export const AMO_PIPELINE_SALES = 9601214; // Продажи
export const AMO_PIPELINE_COMPLAINTS = 9601246; // Жалобы / возвраты / обмены

export const RENTAL_SERVICE_PRICE = 6500;

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
  sara: "Сара",
} as const;

export type AmoLeadFieldKey = keyof typeof AMO_LEAD_FIELDS;

// Поля лида, которые нужно создать в amoCRM при bootstrap, если их ещё нет.
export const AMO_NEW_LEAD_FIELDS: Array<{ key: AmoLeadFieldKey; type: "date" | "text" }> = [
  { key: "actualUntil", type: "date" },
  { key: "rentalFrom", type: "date" },
  { key: "referredBy", type: "text" },
  { key: "callManager", type: "text" },
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
