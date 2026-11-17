import type { Deal, DealKind } from "@kassa/shared";
import { getEnv } from "../env.js";

export const TYPE_LABELS: Partial<Record<DealKind, string>> = {
  company: "Продажа компании", rental: "Аренда", cert_plastic: "Сертификат пластик",
  cert_digital: "Сертификат электронный", sliv: "Слив", no_sliv: "Не слив",
  defect: "Дефект", drycleaning: "Химчистка", resew: "Перешив",
  wrong_size: "Перепутан размер", wrong_label: "Некорректная бирка", refund: "Возврат", exchange: "Обмен",
};
const complaints = new Set<DealKind>(["defect", "drycleaning", "resew", "wrong_size", "wrong_label", "refund", "exchange"]);
export const MIGRATION_TAG = "MB_MIGRATION_IN_PROGRESS";
export function migrationBlocked(lead: { _embedded?: { tags?: Array<{ name?: string }> } }): boolean {
  return (lead._embedded?.tags ?? []).some(tag => tag.name?.trim() === MIGRATION_TAG);
}
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
  if (deal.kind === "deferred" || deal.kind === "promise") {
    throw new Error("У старой отложки или обещания не определён базовый вид заявки. Нужна классификация до переноса.");
  }
  if (TYPE_LABELS[deal.kind]) return TYPE_LABELS[deal.kind]!;
  if (deal.kind === "delivery") return "Продажа онлайн";
  if (deal.kind !== "sale" || !["online", "offline"].includes(deal.funnel)) {
    throw new Error("Неоднозначный вид заявки для маршрутизации amoCRM");
  }
  return deal.funnel === "online" ? "Продажа онлайн" : "Продажа офлайн";
}

type TypedLead = { pipeline_id?: number; custom_fields_values?: Array<{ field_id: number; values?: Array<{ value?: unknown }> }> | null };

/** Exact reverse mapping. No inference from phone, store, or pipeline position. */
export function decodeLeadType(lead: TypedLead, pipelines: Record<string, number>): Pick<Deal, "kind" | "funnel"> | null {
  const values = lead.custom_fields_values?.filter(f => f.field_id === 3831526)
    .flatMap(f => f.values ?? []).map(v => String(v.value ?? "")).filter(Boolean) ?? [];
  const typedPipeline = Object.values(pipelines).includes(Number(lead.pipeline_id));
  if (!values.length && !typedPipeline) return null;
  if (values.length !== 1) throw new Error("Тип заявки не заполнен или неоднозначен");
  const label = values[0]!;
  const inverse = Object.entries(TYPE_LABELS).find(([, name]) => name === label)?.[0] as DealKind | undefined;
  const kind: DealKind | undefined = label === "Продажа офлайн" || label === "Продажа онлайн" ? "sale" : inverse;
  if (!kind) throw new Error("Неизвестное значение Типа заявки");
  const funnel: Deal["funnel"] = label === "Продажа онлайн" || kind === "cert_digital" ? "online" :
    ["refund", "exchange"].includes(kind) ? "return" :
    ["defect", "drycleaning", "resew", "wrong_size", "wrong_label"].includes(kind) ? "defects" : "offline";
  const targetName = kind === "company" ? "Продажа компании" : kind === "rental" ? "Аренда" :
    funnel === "online" ? "Продажа онлайн" : "Продажа офлайн";
  if (typedPipeline && (complaints.has(kind) || ["sliv", "no_sliv"].includes(kind) || pipelines[targetName] !== lead.pipeline_id)) {
    throw new Error("Тип заявки противоречит воронке. Требуется разбор, автоматическая смена вида запрещена.");
  }
  return { kind, funnel };
}

export function readTypedLead(lead: TypedLead): Pick<Deal, "kind" | "funnel"> | null {
  return typedRoutingEnabled() ? decodeLeadType(lead, typedPipelines()) : null;
}

/** Delivery is an execution mode of an online sale, not a replacement for rental/certificate/complaint. */
export function typedExecutionKind(typed: Pick<Deal, "kind" | "funnel">, stage: string, currentKind?: DealKind): DealKind {
  if (typed.kind !== "sale" || typed.funnel !== "online") return typed.kind;
  const delivery = ["Передан на сборку", "Собран", "Вызван курьер", "Отправлен", "Доставлен", "Не выкуплен"];
  return currentKind === "delivery" || delivery.includes(stage) ? "delivery" : "sale";
}

export function assertTypedBinding(typed: Pick<Deal, "kind" | "funnel"> | null, local: Pick<Deal, "kind" | "funnel">): void {
  if (typed && typeLabel(typed) !== typeLabel(local)) {
    throw new Error("Тип заявки amoCRM противоречит связанной заявке кассы. Требуется ручной разбор.");
  }
}
export function targetPipeline(deal: Pick<Deal, "kind" | "funnel">): number {
  const env = getEnv();
  if (complaints.has(deal.kind) || (typedRoutingEnabled() && ["sliv", "no_sliv"].includes(deal.kind))) return env.AMOCRM_PIPELINE_COMPLAINTS;
  if (!typedRoutingEnabled()) return env.AMOCRM_PIPELINE_SALES;
  // Validate legacy promise/deferred and ambiguous sale channel before selecting.
  typeLabel(deal);
  const name = deal.kind === "company" ? "Продажа компании" : deal.kind === "rental" ? "Аренда" :
    deal.kind === "cert_digital" || deal.kind === "delivery" || deal.funnel === "online" ? "Продажа онлайн" : "Продажа офлайн";
  const id = typedPipelines()[name];
  if (!id) throw new Error(`Не настроена воронка «${name}»`);
  return id;
}
