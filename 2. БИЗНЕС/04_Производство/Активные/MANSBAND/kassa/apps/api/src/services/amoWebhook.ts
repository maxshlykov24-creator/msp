import * as amo from "../clients/amo.js";
import * as deals from "./deals.js";
import * as taskFlow from "./taskFlow.js";
import { appendSystemAudit } from "./audit.js";

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
  if (keys.length === 0) return;
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
export async function applyAmoLeadStatusEvent(event: LeadStatusEvent): Promise<{
  dealNumber?: number;
  stage?: string;
  tasks: boolean;
  skipped?: string;
}> {
  let statusId = event.statusId;
  let pipelineId = event.pipelineId;
  let lead: amo.AmoLead | null = null;

  if (!statusId || !pipelineId) {
    lead = await amo.getLead(event.id);
    if (!lead) return { skipped: "lead_not_found", tasks: false };
    statusId = statusId ?? lead.status_id;
    pipelineId = pipelineId ?? lead.pipeline_id;
  }

  const stage = await deals.stageNameByStatusId(statusId!, pipelineId);
  if (!stage || stage === "—") return { skipped: "unknown_status", tasks: false };

  let deal = await deals.getByAmoLeadId(event.id);
  if (!deal) {
    lead = lead ?? (await amo.getLead(event.id));
    if (!lead?.id) return { skipped: "lead_not_found", tasks: false };
    deal = await deals.ensureLocalFromAmoLead(lead, stage);
  }

  const nextKind =
    DELIVERY_STAGES.has(stage) && deal.kind !== "delivery" && deal.kind !== "company"
      ? "delivery"
      : deal.kind;

  if (deal.stage === stage && deal.kind === nextKind) {
    // Этап уже совпал — задачи всё равно досоздаём (если вебхук пришёл повторно
    // после сбоя постановки).
    await taskFlow.onDealStage(deal, ACTOR);
    return { dealNumber: deal.number, stage, tasks: true, skipped: "stage_unchanged" };
  }

  const updated = await deals.updateStage(String(deal.number), stage, ACTOR, {
    skipAmoWriteback: true,
    kind: nextKind !== deal.kind ? nextKind : undefined,
    reason: "Синхронизация этапа из amoCRM",
  });
  if (!updated) return { skipped: "update_failed", tasks: false };

  // П9: синк из amo — авто-событие в истории изменений.
  await appendSystemAudit({
    action: `Заявка #${deal.number}: этап «${deal.stage}» → «${stage}» (синхронизация из amoCRM)`,
    entityType: "deal",
    entityId: String(deal.number),
    before: { stage: deal.stage, kind: deal.kind },
    after: { stage, kind: nextKind },
  });

  await taskFlow.onDealStage(updated, ACTOR);
  deals.invalidateAmoOpenCache();
  return { dealNumber: updated.number, stage: updated.stage, tasks: true };
}

export async function handleAmoWebhook(rawBody: unknown): Promise<{
  ok: true;
  processed: number;
  results: Awaited<ReturnType<typeof applyAmoLeadStatusEvent>>[];
}> {
  const body = parseAmoWebhookBody(rawBody);
  const events = extractLeadStatusEvents(body);
  const results: Awaited<ReturnType<typeof applyAmoLeadStatusEvent>>[] = [];
  for (const event of events) {
    try {
      results.push(await applyAmoLeadStatusEvent(event));
    } catch (err) {
      results.push({
        tasks: false,
        skipped: err instanceof Error ? err.message : "error",
      });
    }
  }
  return { ok: true, processed: events.length, results };
}

export function isDeliveryStage(stage: string): boolean {
  return DELIVERY_STAGES.has(stage);
}
