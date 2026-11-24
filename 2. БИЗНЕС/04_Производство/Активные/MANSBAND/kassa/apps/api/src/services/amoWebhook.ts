import { AMO_PIPELINE_SALES, type DealKind } from "@kassa/shared";
import * as amo from "../clients/amo.js";
import * as deals from "./deals.js";
import * as taskFlow from "./taskFlow.js";
import { writeKassaLink } from "./amoMapping.js";
import { appendSystemAudit } from "./audit.js";
import { allowedAmoPipeline, migrationBlocked, readTypedLead, assertTypedBinding, typedExecutionKind } from "./amoRouting.js";
import { withOperationLock } from "../lib/idempotency.js";
import { performAmoStatusPlan, planAmoStatus } from "./amoStatusPolicy.js";

/**
 * Этапы воронки доставки (СДЭК). При приходе из amo на такой этап
 * заявка в кассе становится kind=delivery и ставятся задачи по TASK_FLOW.
 */
const DELIVERY_STAGES = new Set([
  "Передан на сборку",
  "Собран",
  "Вызван курьер",
  "Отправлен",
  "Доставлен",
  "Не выкуплен",
]);

const ACTOR = "amoCRM";

type LeadStatusEvent = {
  id: number;
  statusId?: number;
  pipelineId?: number;
};

/** Разбор nested keys leads[status][0][id] из form-urlencoded amo. */
export function parseAmoWebhookBody(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object" && !Buffer.isBuffer(raw) && !(raw instanceof Uint8Array)) {
    return raw as Record<string, unknown>;
  }
  const str =
    typeof raw === "string"
      ? raw
      : Buffer.isBuffer(raw) || raw instanceof Uint8Array
        ? Buffer.from(raw).toString("utf8")
        : "";
  if (!str.trim()) return {};
  if (str.trimStart().startsWith("{")) {
    try {
      return JSON.parse(str) as Record<string, unknown>;
    } catch {
      /* form */
    }
  }
  const root: Record<string, unknown> = {};
  const params = new URLSearchParams(str);
  for (const [key, value] of params.entries()) {
    setFormPath(root, key, value);
  }
  return root;
}

function setFormPath(root: Record<string, unknown>, path: string, value: string): void {
  const keys = [...path.matchAll(/([^[\]]+)/g)].map((m) => m[1]!);
  if (keys.length === 0 || keys.some(key => ["__proto__", "constructor", "prototype"].includes(key))) return;
  let cur: unknown = root;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]!;
    const next = keys[i + 1]!;
    const nextIsNum = /^\d+$/.test(next);
    const obj = cur as Record<string, unknown>;
    if (obj[k] == null) obj[k] = nextIsNum ? [] : {};
    cur = obj[k];
  }
  (cur as Record<string, unknown>)[keys[keys.length - 1]!] = value;
}

function asRows(bucket: unknown): Array<Record<string, unknown>> {
  if (!bucket) return [];
  if (Array.isArray(bucket)) {
    return bucket.filter((r): r is Record<string, unknown> => !!r && typeof r === "object");
  }
  if (typeof bucket === "object") {
    return Object.values(bucket as Record<string, unknown>).filter(
      (r): r is Record<string, unknown> => !!r && typeof r === "object" && !Array.isArray(r)
    );
  }
  return [];
}

/** События смены этапа / обновления сделки из тела вебхука. */
export function extractLeadStatusEvents(body: Record<string, unknown>): LeadStatusEvent[] {
  const leads = ((body.leads ?? body) as Record<string, unknown>) || {};
  const out: LeadStatusEvent[] = [];
  const seen = new Set<number>();
  for (const key of ["status", "update"] as const) {
    for (const row of asRows(leads[key])) {
      const id = Number(row.id);
      if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue;
      seen.add(id);
      const statusId = Number(row.status_id);
      const pipelineId = Number(row.pipeline_id);
      out.push({
        id,
        statusId: Number.isFinite(statusId) && statusId > 0 ? statusId : undefined,
        pipelineId: Number.isFinite(pipelineId) && pipelineId > 0 ? pipelineId : undefined,
      });
    }
  }
  return out;
}

/**
 * amo сменил этап → зеркало в кассе + задачи (СДЭК и пр.).
 * В amo обратно не пишем (иначе цикл webhook ↔ writeback).
 */
export async function applyAmoLeadStatusEvent(event: LeadStatusEvent): Promise<Awaited<ReturnType<typeof applyAmoLeadStatusEventLocked>>> {
  return withOperationLock(`amo-sync:${event.id}`, () => applyAmoLeadStatusEventLocked(event));
}

function inboundKind(stage: string, current: DealKind, typed: ReturnType<typeof readTypedLead>): DealKind {
  return typed ? typedExecutionKind(typed, stage, current) : deals.kindForAmoStage(stage, current);
}

async function applyAmoLeadStatusEventLocked(event: LeadStatusEvent): Promise<{
  dealNumber?: number;
  stage?: string;
  tasks: boolean;
  skipped?: string;
}> {
  // События могут прийти повторно и не по порядку: читаем актуальное состояние CRM.
  const lead = await amo.getLead(event.id);
  if (!lead?.id) return { skipped: "lead_not_found", tasks: false };
  if (migrationBlocked(lead)) return { skipped: "migration_guard", tasks: false };
  const pipelineAllowed = allowedAmoPipeline(lead.pipeline_id);
  const stage = lead.status_id ? await deals.stageNameByStatusId(lead.status_id, lead.pipeline_id) : null;
  const deal = pipelineAllowed ? await deals.getByAmoLeadId(lead.id) : null;
  const typed = readTypedLead(lead);
  if (deal) assertTypedBinding(typed, deal);
  const decision = planAmoStatus({
    pipelineAllowed,
    isSalesPipeline: lead.pipeline_id === AMO_PIPELINE_SALES,
    stage,
    deal: deal ? {
      number: deal.number,
      kind: deal.kind,
      stage: deal.stage,
      syncStatus: deal.syncStatus,
      amoEditOutbox: deal.amoEditOutbox,
      moneyCancellation: deal.moneyCancellation,
      rentalTo: deal.rentalTo,
    } : null,
  });
  return performAmoStatusPlan({
    decision,
    leadId: lead.id,
    existing: deal ? { number: deal.number, stage: deal.stage, kind: deal.kind } : null,
  }, {
    createMirror: async () => {
      const created = await deals.createAmoStatusMirror(lead, stage ?? "");
      return { number: created.number, stage: created.stage, kind: created.kind };
    },
    saveStage: async (target) => {
      const saved = await deals.applyInboundAmoStage(deal!, lead, target, inboundKind(target, deal!.kind, typed));
      return { number: saved.deal.number, stage: saved.deal.stage, kind: saved.deal.kind, changed: saved.changed };
    },
    onTasks: async () => {
      const current = await deals.getByAmoLeadId(lead.id);
      if (current) await taskFlow.onDealStage(current, ACTOR);
    },
    writeLink: (dealNumber) => writeKassaLink(lead.id, dealNumber),
    audit: async (fromStage, toStage, dealNumber) => {
      await appendSystemAudit({
        action: `Заявка #${dealNumber}: этап «${fromStage}» → «${toStage}» (синхронизация из amoCRM)`,
        entityType: "deal",
        entityId: String(dealNumber),
        before: { stage: fromStage },
        after: { stage: toStage },
      });
    },
    invalidate: () => deals.invalidateAmoOpenCache(),
  });
}

export async function handleAmoWebhook(rawBody: unknown, applyEvent = applyAmoLeadStatusEvent): Promise<{
  ok: true;
  processed: number;
  results: Awaited<ReturnType<typeof applyAmoLeadStatusEvent>>[];
}> {
  const body = parseAmoWebhookBody(rawBody);
  const events = extractLeadStatusEvents(body);
  const results: Awaited<ReturnType<typeof applyAmoLeadStatusEvent>>[] = [];
  const failures: number[] = [];
  for (const event of events) {
    try {
      results.push(await applyEvent(event));
    } catch {
      failures.push(event.id);
    }
  }
  if (failures.length) throw new Error(`Не обработаны события amoCRM: ${failures.join(", ")}`);
  return { ok: true, processed: events.length, results };
}

export function isDeliveryStage(stage: string): boolean {
  return DELIVERY_STAGES.has(stage);
}
