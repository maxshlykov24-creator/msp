import { randomUUID } from "node:crypto";
import type { Deal } from "@kassa/shared";
import { sql } from "../db/index.js";
import { withOperationLock } from "../lib/idempotency.js";
import * as amo from "../clients/amo.js";
import { buildEditedLeadFields } from "./amoMapping.js";
import { getEnv } from "../env.js";

export async function prepareAmoEdit(deal: Deal, before: Deal): Promise<void> {
  if (!deal.amoLeadId) return;
  const next = await buildEditedLeadFields(deal, before);
  const merged = new Map((before.amoEditOutbox?.customFields ?? []).map(f => [f.field_id, f]));
  for (const field of next) merged.set(field.field_id, field);
  deal.amoEditOutbox = {
    id: randomUUID(), attempts: 0, retryAt: 0,
    stage: deal.stage !== before.stage ? deal.stage : before.amoEditOutbox?.stage,
    price: deal.total !== before.total ? Math.abs(deal.total) : before.amoEditOutbox?.price,
    customFields: [...merged.values()],
  };
  deal.syncStatus = "pending";
  deal.syncError = undefined;
}

export async function flushAmoEdit(number: number): Promise<void> {
  const { getByNumber, resolveStatusId } = await import("./deals.js");
  const first = await getByNumber(number);
  if (!first?.amoLeadId || !first.amoEditOutbox) return;
  await withOperationLock(`amo-sync:${first.amoLeadId}`, async () => {
    const deal = await getByNumber(number);
    const job = deal?.amoEditOutbox;
    if (!deal?.amoLeadId || !job || job.attempts >= 8 || job.retryAt > Date.now()) return;
    if (deal.moneyCancellation) return;
    try {
      const statusId = job.stage ? await resolveStatusId(deal.amoLeadId, job.stage) : undefined;
      if (job.stage && !statusId) throw new Error("Этап отсутствует в фактической воронке");
      await amo.updateLead(deal.amoLeadId, {statusId: statusId ?? undefined, price:job.price,customFields:job.customFields});
      // Never replace the full deal snapshot after a network request.
      await sql`update deals set data = (data - 'amoEditOutbox' - 'syncError') || '{"syncStatus":"synced"}'::jsonb,
        sync_status = 'synced', sync_error = null, updated_at = now()
        where number = ${number} and data->'amoEditOutbox'->>'id' = ${job.id}`;
    } catch (error) {
      const retry = {...job,attempts:job.attempts+1,retryAt:Date.now()+Math.min(300000,5000*2**job.attempts)};
      const state = {amoEditOutbox:retry,syncStatus:"failed",syncError:`amo-edit:${(error as Error).message}`};
      await sql`update deals set data = data || ${JSON.stringify(state)}::jsonb, sync_status = 'failed', sync_error = ${state.syncError}, updated_at = now()
        where number = ${number} and data->'amoEditOutbox'->>'id' = ${job.id}`;
    }
  });
}

let running = false;
export async function flushAmoEdits(): Promise<void> {
  if (running || getEnv().AMOCRM_ASYNC_EDITS !== "true") return;
  running = true;
  try {
    const rows = await sql<{number:number}[]>`select number from deals where data ? 'amoEditOutbox'
      and not (data ? 'moneyCancellation')
      and (data->'amoEditOutbox'->>'attempts')::int < 8
      and (data->'amoEditOutbox'->>'retryAt')::bigint <= ${Date.now()}
      order by updated_at limit 20`;
    for (const row of rows) await flushAmoEdit(row.number).catch(() => {});
  } finally { running = false; }
}
