import type { Deal, DealKind } from "@kassa/shared";
import { getEnv } from "../env.js";

export const TYPE_LABELS: Partial<Record<DealKind, string>> = {
  company: "Продажа компании", rental: "Аренда", cert_plastic: "Сертификат пластик",
  cert_digital: "Сертификат электронный", sliv: "Слив", no_sliv: "Не слив",
  defect: "Дефект", drycleaning: "Химчистка", resew: "Перешив",
  wrong_size: "Перепутан размер", wrong_label: "Некорректная бирка", refund: "Возврат", exchange: "Обмен",
};
const complaints = new Set<DealKind>(["defect", "drycleaning", "resew", "wrong_size", "wrong_label", "refund", "exchange"]);
export function typedRoutingEnabled(): boolean { return getEnv().AMOCRM_TYPED_PIPELINES_ENABLED === "true"; }
export function typedPipelines(): Record<string, number> {
  const parsed: unknown = JSON.parse(getEnv().AMOCRM_TYPED_PIPELINES);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Некорректная карта воронок");
  for (const id of Object.values(parsed)) if (!Number.isSafeInteger(id) || Number(id) <= 0) throw new Error("Некорректный ID воронки");
  return parsed as Record<string, number>;
}
export function allowedAmoPipeline(id: number | undefined): boolean {
  const env = getEnv();
  return id === env.AMOCRM_PIPELINE_SALES || id === env.AMOCRM_PIPELINE_COMPLAINTS ||
    (typedRoutingEnabled() && Object.values(typedPipelines()).includes(Number(id)));
}
export function typeLabel(deal: Pick<Deal, "kind" | "funnel">): string {
  return TYPE_LABELS[deal.kind] ?? (deal.funnel === "online" || deal.kind === "delivery" ? "Продажа онлайн" : "Продажа офлайн");
}
export function targetPipeline(deal: Pick<Deal, "kind" | "funnel">): number {
  const env = getEnv();
  if (complaints.has(deal.kind) || (typedRoutingEnabled() && ["sliv", "no_sliv"].includes(deal.kind))) return env.AMOCRM_PIPELINE_COMPLAINTS;
  if (!typedRoutingEnabled()) return env.AMOCRM_PIPELINE_SALES;
  const name = deal.kind === "company" ? "Продажа компании" : deal.kind === "rental" ? "Аренда" :
    deal.kind === "cert_digital" || deal.kind === "delivery" || deal.funnel === "online" ? "Продажа онлайн" : "Продажа офлайн";
  const id = typedPipelines()[name];
  if (!id) throw new Error(`Не настроена воронка «${name}»`);
  return id;
}
