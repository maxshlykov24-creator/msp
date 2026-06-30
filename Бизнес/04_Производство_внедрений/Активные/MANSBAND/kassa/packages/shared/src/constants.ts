import type { Store } from "./types.js";

// Справочники, общие для фронта и бэка. Реальные значения из amoCRM / МойСклад / ТЗ.

export const STORES = ["На Бауманской", "На Пятницкой", "Онлайн-магазин"] as const;

export const STORE_ADDRESS: Record<Store, string> = {
  "На Бауманской": "г. Москва, Спартаковская пл., д. 14, стр. 2",
  "На Пятницкой": "г. Москва, ул. Пятницкая, д. 8",
  "Онлайн-магазин": "Онлайн · отправка",
};

// Маппинг шоурум → название склада МойСклад (источник отгрузки).
// NB: точное имя/href склада Пятницкой сверяется при первом синке каталога
// (в снимке 2026-06-02 фигурировал склад «На Новокузнецкой») — не хардкодим href.
export const STORE_TO_WAREHOUSE: Record<Store, string | null> = {
  "На Бауманской": "На Бауманской",
  "На Пятницкой": "На Пятницкой",
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

// Имена кастом-полей amoCRM для writeback (точные field_id резолвятся по имени при bootstrap-синке).
export const AMO_WRITEBACK_FIELDS = {
  msOrder: "Заказ МойСклад",
  msDemand: "№ Заказа/Отгрузки/Счёта",
  paymentStatus: "Статус оплаты",
} as const;
