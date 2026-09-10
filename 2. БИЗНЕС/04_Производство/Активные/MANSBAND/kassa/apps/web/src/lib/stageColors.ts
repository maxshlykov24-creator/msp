/** Пастельные цвета этапов amoCRM — приглушённые, для тёмного UI кассы */

export type StageStyle = {
  bg: string;
  text: string;
  ring: string;
};

const palette: Record<string, StageStyle> = {
  new: {
    bg: "rgba(125, 172, 228, 0.17)",
    text: "rgb(186, 214, 245)",
    ring: "rgba(125, 172, 228, 0.28)",
  },
  neutral: {
    bg: "rgba(148, 163, 184, 0.14)",
    text: "rgb(203, 213, 225)",
    ring: "rgba(148, 163, 184, 0.24)",
  },
  active: {
    bg: "rgba(118, 162, 220, 0.19)",
    text: "rgb(190, 218, 248)",
    ring: "rgba(118, 162, 220, 0.30)",
  },
  negative: {
    bg: "rgba(232, 155, 155, 0.15)",
    text: "rgb(248, 196, 196)",
    ring: "rgba(232, 155, 155, 0.28)",
  },
  inventory: {
    bg: "rgba(224, 178, 128, 0.17)",
    text: "rgb(245, 211, 170)",
    ring: "rgba(224, 178, 128, 0.28)",
  },
  meeting: {
    bg: "rgba(236, 218, 140, 0.19)",
    text: "rgb(245, 230, 175)",
    ring: "rgba(236, 218, 140, 0.32)",
  },
  rental: {
    bg: "rgba(190, 170, 220, 0.17)",
    text: "rgb(218, 205, 242)",
    ring: "rgba(190, 170, 220, 0.28)",
  },
  logistics: {
    bg: "rgba(230, 210, 130, 0.17)",
    text: "rgb(242, 228, 178)",
    ring: "rgba(230, 210, 130, 0.28)",
  },
  success: {
    bg: "rgba(152, 198, 142, 0.19)",
    text: "rgb(198, 230, 190)",
    ring: "rgba(152, 198, 142, 0.32)",
  },
  fail: {
    bg: "rgba(120, 125, 135, 0.22)",
    text: "rgb(180, 185, 195)",
    ring: "rgba(120, 125, 135, 0.32)",
  },
  default: {
    bg: "rgba(148, 163, 184, 0.11)",
    text: "rgb(190, 198, 210)",
    ring: "rgba(148, 163, 184, 0.20)",
  },
};

/** Этап → группа палитры (как в воронке amoCRM) */
const stageToGroup: Record<string, keyof typeof palette> = {
  "Новая заявка": "new",

  Спам: "neutral",
  Сара: "neutral",
  "Сдвоенная заявка": "neutral",
  "Сертификат оплачен": "neutral",

  "Взято в работу": "active",
  "Дано обещание": "active",
  "Хочет прийти": "active",
  "Хочет заказать": "active",
  "Хочет прийти/заказать": "active",
  "Хочет прийти / заказать": "active",

  "Не готов прийти / заказать": "negative",
  "Не готов прийти/заказать": "negative",
  "Не оказалось размера": "negative",
  "Ушел смотреть у конкурентов": "negative",
  "Ушёл смотреть у конкурентов": "negative",
  "Ушёл к конкурентам": "negative",
  "Не выкуплен": "negative",

  "Ждет товар": "inventory",
  "Ждёт товар": "inventory",
  "Товар в пути": "logistics",
  "Товар в магазине": "inventory",
  "Товар отложен": "inventory",
  "Ждёт оплату": "active",
  "Ждет оплату": "active",

  "Встреча назначена": "meeting",

  "Аренда оплачена": "rental",
  "В аренде": "rental",
  Возвращена: "rental",

  "Счёт запрошен": "new",
  "Счет запрошен": "new",
  "Счет выставлен": "active",
  "Счёт выставлен": "active",
  Оплачено: "success",
  "Документы готовы": "logistics",
  "Документы переданы": "logistics",

  "Передан на сборку": "logistics",
  Собран: "logistics",
  "Вызван курьер": "logistics",
  Отправлен: "logistics",
  Доставлен: "logistics",

  Успех: "success",
  Провал: "fail",
};

export function getStageGroup(stage: string): keyof typeof palette {
  return stageToGroup[stage] ?? "default";
}

export function getStageStyle(stage: string): StageStyle {
  return palette[getStageGroup(stage)];
}
