import { rentalEndPassed, type DealKind } from "@kassa/shared";

/** Этапы, на которых сделка из amo впервые становится заявкой кассы. */
export const AMO_MIRROR_STAGES = new Set([
  "Ждет товар",
  "Товар в магазине",
  "Дано обещание",
  "Передан на сборку",
  "Собран",
  "Вызван курьер",
  "Отправлен",
  "Доставлен",
  "Не выкуплен",
]);

export type AmoStatusDeal = {
  number: number;
  kind: DealKind;
  stage: string;
  syncStatus?: string;
  amoEditOutbox?: unknown;
  moneyCancellation?: unknown;
  rentalTo?: string;
};

export type AmoStatusDecision =
  | { type: "skip"; reason: string }
  | { type: "keep"; reason: string; dealNumber: number }
  | { type: "create" }
  | { type: "sync"; stage: string; tasks: boolean };

export function planAmoStatus(input: {
  pipelineAllowed: boolean;
  isSalesPipeline: boolean;
  stage: string | null;
  deal: AmoStatusDeal | null;
}): AmoStatusDecision {
  if (!input.pipelineAllowed) return { type: "skip", reason: "pipeline_disabled" };
  const stage = input.stage ?? "";
  if (!stage || stage === "—") return { type: "skip", reason: "unknown_status" };
  const deal = input.deal;
  if (deal?.moneyCancellation) return { type: "keep", reason: "money_cancellation", dealNumber: deal.number };
  if (deal?.amoEditOutbox) return { type: "keep", reason: "local_edit_pending", dealNumber: deal.number };
  if (deal?.syncStatus === "failed") return { type: "keep", reason: "local_ahead", dealNumber: deal.number };
  if (deal?.kind === "rental" && stage === "Аренда оплачена" && rentalEndPassed(deal.rentalTo)) {
    return { type: "keep", reason: "rental_overdue", dealNumber: deal.number };
  }
  if (deal?.kind === "no_sliv" && stage === "Провал") {
    if (deal.stage === "Не слив") return { type: "keep", reason: "no_sliv", dealNumber: deal.number };
    return { type: "sync", stage: "Не слив", tasks: false };
  }
  if (!deal) {
    if (!input.isSalesPipeline || !AMO_MIRROR_STAGES.has(stage)) return { type: "skip", reason: "no_local_effect" };
    return { type: "create" };
  }
  return { type: "sync", stage, tasks: true };
}

type DescriptiveDeal = {
  stage: string;
  kind: DealKind;
  syncStatus?: string;
  amoEditOutbox?: unknown;
  moneyCancellation?: unknown;
  history?: Array<{ at: string; who: string; action: string }>;
  completedAt?: string;
  consultant?: string;
  purpose?: string;
  channel?: string;
  comment?: string;
  referredBy?: string;
  callManager?: string;
};

const DESCRIPTIVE_KEYS = ["consultant", "purpose", "channel", "comment", "referredBy", "callManager"] as const;

/** Меняет только этап, вид и шесть описательных полей. Остальной объект остаётся тем же. */
export function mergeInboundAmoStage<T extends DescriptiveDeal>(
  deal: T,
  fields: Partial<Pick<T, (typeof DESCRIPTIVE_KEYS)[number]>>,
  stage: string,
  kind: DealKind,
  now = new Date().toISOString()
): { deal: T; changed: boolean } {
  if (deal.moneyCancellation || deal.syncStatus === "failed" || deal.amoEditOutbox) return { deal, changed: false };
  const next: T = { ...deal, ...fields, stage, kind };
  if (stage === "Успех" && !next.completedAt) next.completedAt = now;
  const sameFields = DESCRIPTIVE_KEYS.every(key => (next[key] ?? "") === (deal[key] ?? ""));
  if (next.stage === deal.stage && next.kind === deal.kind && sameFields) return { deal, changed: false };
  if (next.stage !== deal.stage || next.kind !== deal.kind) {
    next.history = [...(deal.history ?? []), {
      at: now,
      who: "amoCRM",
      action: `Этап изменён: ${deal.stage} → ${stage} · Синхронизация из amoCRM`,
    }];
  }
  return { deal: next, changed: true };
}

export type AmoStatusSnapshot = { number: number; stage: string; kind: DealKind };

export type AmoStatusResult = {
  dealNumber?: number;
  stage?: string;
  tasks: boolean;
  skipped?: string;
};

/**
 * Исполняет уже принятое решение. Денег, сертификатов и обратной записи этапа здесь нет:
 * порты умеют только создать зеркало, записать этап и поставить задачи.
 */
export async function performAmoStatusPlan(input: {
  decision: AmoStatusDecision;
  leadId: number;
  existing: AmoStatusSnapshot | null;
}, ports: {
  createMirror: () => Promise<AmoStatusSnapshot>;
  saveStage: (stage: string) => Promise<AmoStatusSnapshot & { changed: boolean }>;
  onTasks: () => Promise<void>;
  writeLink: (dealNumber: number) => Promise<void>;
  audit: (fromStage: string, toStage: string, dealNumber: number) => Promise<void>;
  invalidate: () => void;
}): Promise<AmoStatusResult> {
  const { decision } = input;
  if (decision.type === "skip") return { skipped: decision.reason, tasks: false };
  if (decision.type === "keep") return { dealNumber: decision.dealNumber, skipped: decision.reason, tasks: false };

  const deal = decision.type === "create" ? await ports.createMirror() : await ports.saveStage(decision.stage);
  await ports.writeLink(deal.number).catch((error: unknown) => {
    console.warn(`[amo] ссылка на кассу, сделка #${input.leadId}: ${(error as Error).message}`);
  });

  if (decision.type === "sync" && input.existing && deal.number === input.existing.number
    && (input.existing.stage !== deal.stage || input.existing.kind !== deal.kind)) {
    await ports.audit(input.existing.stage, deal.stage, deal.number);
    ports.invalidate();
  }
  if (decision.type === "create") ports.invalidate();

  const tasks = decision.type === "create" || decision.tasks;
  if (tasks) await ports.onTasks();
  const unchanged = decision.type === "sync" && input.existing?.stage === deal.stage && input.existing.kind === deal.kind;
  return {
    dealNumber: deal.number,
    stage: deal.stage,
    tasks,
    skipped: unchanged ? "stage_unchanged" : undefined,
  };
}
