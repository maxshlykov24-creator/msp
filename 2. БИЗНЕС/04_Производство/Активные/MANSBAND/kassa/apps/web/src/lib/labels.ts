import type { DealKind, FunnelType } from "../data/types";

export const FUNNEL_LABEL: Record<FunnelType, string> = {
  offline: "Оффлайн",
  online: "Онлайн",
  defects: "Дефекты",
  return: "Возврат / обмен",
};

export const KIND_LABEL: Record<DealKind, string> = {
  sale: "Продажа",
  company: "Продажа компании",
  cert_plastic: "Сертификат (пластик)",
  rental: "Аренда",
  deferred: "Отложка",
  promise: "Обещание",
  no_sliv: "Не слив",
  sliv: "Слив",
  cert_digital: "Сертификат (электр.)",
  delivery: "Доставка (СДЭК)",
  defect: "Дефекты",
  drycleaning: "Химчистка",
  resew: "Перешив",
  wrong_size: "Перепутан размер",
  wrong_label: "Некорректная бирка",
  refund: "Возврат",
  exchange: "Обмен",
};

/** @deprecated Используйте StageBadge + getStageStyle из stageColors.ts */
export function stageTone(stage: string): "green" | "red" | "amber" | "blue" | "gold" | "gray" {
  if (stage === "Успех") return "green";
  if (stage === "Провал") return "red";
  if (["В аренде", "Товар отложен", "Дано обещание", "Аренда оплачена"].includes(stage)) return "amber";
  if (["Отправлен", "Доставлен", "Передан на сборку", "Собран", "Вызван курьер"].includes(stage)) return "blue";
  if (stage === "Сертификат оплачен") return "gold";
  return "gray";
}
