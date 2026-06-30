import { desc, eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, amoMeta } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import type { Deal } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

// ── Локальное зеркало заявок (то, что знает касса) ──────────────────

export async function listLocal(): Promise<Deal[]> {
  const rows = await db.select().from(dealsTable).orderBy(desc(dealsTable.number)).limit(500);
  return rows.map((r) => r.data as Deal);
}

export async function getByNumber(n: number): Promise<Deal | null> {
  const rows = await db.select().from(dealsTable).where(eq(dealsTable.number, n)).limit(1);
  return (rows[0]?.data as Deal | undefined) ?? null;
}

// Резолв по ссылке с фронта: это может быть number (строкой) или Deal.id (строка).
export async function resolve(ref: string): Promise<Deal | null> {
  const asNum = Number(ref);
  if (Number.isInteger(asNum) && String(asNum) === ref) {
    const byNum = await getByNumber(asNum);
    if (byNum) return byNum;
  }
  const all = await db.select().from(dealsTable).limit(1000);
  return (all.find((r) => (r.data as Deal).id === ref)?.data as Deal | undefined) ?? null;
}

export async function persist(deal: Deal): Promise<Deal> {
  await db
    .insert(dealsTable)
    .values({
      number: deal.number,
      amoLeadId: deal.amoLeadId ?? null,
      msOrderId: deal.msOrderId ?? null,
      msDemandId: deal.msDemandId ?? null,
      data: deal as object,
      stage: deal.stage,
      paymentStatus: deal.paymentStatus ?? null,
      updatedAt: new Date(),
    })
    .onConflictDoUpdate({
      target: dealsTable.number,
      set: {
        amoLeadId: deal.amoLeadId ?? null,
        msOrderId: deal.msOrderId ?? null,
        msDemandId: deal.msDemandId ?? null,
        data: deal as object,
        stage: deal.stage,
        paymentStatus: deal.paymentStatus ?? null,
        updatedAt: new Date(),
      },
    });
  return deal;
}

export async function updateStage(ref: string, stage: string, who: string): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const updated: Deal = {
    ...deal,
    stage,
    history: [
      ...(deal.history ?? []),
      { at: new Date().toISOString(), who, action: `Этап изменён: ${deal.stage} → ${stage}` },
    ],
  };
  await persist(updated);
  // Синхронизируем этап в amoCRM, если связана сделка.
  if (deal.amoLeadId) {
    const statusId = await resolveStatusId(deal.amoLeadId, stage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
  }
  broadcast("deal.stage_changed", { number: deal.number, stage });
  return updated;
}

export async function addComment(ref: string, text: string, who: string): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const updated: Deal = {
    ...deal,
    history: [...(deal.history ?? []), { at: new Date().toISOString(), who, action: `Комментарий: ${text}` }],
  };
  await persist(updated);
  if (deal.amoLeadId) await amo.addLeadNote(deal.amoLeadId, text).catch(() => {});
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

async function resolveStatusId(leadId: number, stageName: string): Promise<number | null> {
  const lead = await amo.getLead(leadId);
  if (!lead) return null;
  const rows = await db.select().from(amoMeta).where(eq(amoMeta.kind, "status"));
  const sameP = rows.filter((r) => r.parentId === lead.pipeline_id);
  const exact = sameP.find((r) => r.name.toLowerCase() === stageName.toLowerCase());
  return exact?.amoId ?? sameP.find((r) => r.name.toLowerCase().includes(stageName.toLowerCase()))?.amoId ?? null;
}

// ── Чтение из amoCRM (реальные заявки для экрана «Заявки») ───────────

function stageName(statusId: number, statuses: Array<{ amoId: number; name: string }>): string {
  return statuses.find((s) => s.amoId === statusId)?.name ?? "Новая заявка";
}

async function statusMap(): Promise<Array<{ amoId: number; name: string }>> {
  const rows = await db.select().from(amoMeta).where(eq(amoMeta.kind, "status"));
  return rows.map((r) => ({ amoId: r.amoId, name: r.name }));
}

function mapLeadToDeal(lead: amo.AmoLead, statuses: Array<{ amoId: number; name: string }>): Deal {
  return {
    id: `amo-${lead.id}`,
    number: lead.id,
    createdAt: new Date().toISOString(),
    funnel: "offline",
    kind: "sale",
    consultant: "",
    clientName: lead.name,
    clientPhone: "",
    store: "На Бауманской",
    items: [],
    payments: [],
    stage: stageName(lead.status_id, statuses),
    total: lead.price ?? 0,
    paid: 0,
    amoLeadId: lead.id,
  };
}

export async function listAmo(params: amo.ListLeadsParams): Promise<Deal[]> {
  const [leads, statuses] = await Promise.all([amo.listLeads(params), statusMap()]);
  return leads.map((l) => mapLeadToDeal(l, statuses));
}

export async function searchAmo(args: { phone?: string; name?: string; number?: number }): Promise<Deal[]> {
  const statuses = await statusMap();
  if (args.number) {
    const lead = await amo.getLead(args.number);
    return lead ? [mapLeadToDeal(lead, statuses)] : [];
  }
  const query = args.phone ?? args.name ?? "";
  const leads = await amo.listLeads({ query, limit: 20 });
  return leads.map((l) => mapLeadToDeal(l, statuses));
}
