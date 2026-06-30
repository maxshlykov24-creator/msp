import { desc, eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { queue, sary } from "../db/schema.js";
import type { QueueItem, SaryPayout } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

// ── Очередь Эдвина (сдача / возврат «не выдано») ────────────────────

function toQueueItem(r: typeof queue.$inferSelect): QueueItem {
  return {
    id: r.id,
    kind: r.kind as QueueItem["kind"],
    dealNumber: r.dealNumber,
    client: r.client,
    amount: r.amount / 100,
    destination: r.destination,
    status: r.status as QueueItem["status"],
    createdAt: r.createdAt.toISOString(),
  };
}

export async function listQueue(): Promise<QueueItem[]> {
  const rows = await db.select().from(queue).orderBy(desc(queue.createdAt));
  return rows.map(toQueueItem);
}

export async function addQueue(item: Omit<QueueItem, "id" | "createdAt" | "status"> & { status?: string }) {
  const [row] = await db
    .insert(queue)
    .values({
      kind: item.kind,
      dealNumber: item.dealNumber,
      client: item.client,
      amount: Math.round(item.amount * 100),
      destination: item.destination,
      status: item.status ?? "pending",
    })
    .returning();
  broadcast("queue.updated", {});
  return row ? toQueueItem(row) : null;
}

export async function issueQueue(id: string) {
  await db.update(queue).set({ status: "issued" }).where(eq(queue.id, id));
  broadcast("queue.updated", {});
}

// ── Сары (реферальные выплаты) ──────────────────────────────────────

function toSary(r: typeof sary.$inferSelect): SaryPayout {
  return {
    id: r.id,
    client: r.client,
    phone: r.phone,
    amount: r.amount / 100,
    reason: r.reason,
    refDealNumber: r.refDealNumber ?? undefined,
    status: r.status as SaryPayout["status"],
    screenshotAttached: r.screenshotAttached,
    createdAt: r.createdAt.toISOString(),
  };
}

export async function listSary(): Promise<SaryPayout[]> {
  const rows = await db.select().from(sary).orderBy(desc(sary.createdAt));
  return rows.map(toSary);
}

export async function markSarySent(id: string) {
  await db.update(sary).set({ status: "sent", screenshotAttached: true }).where(eq(sary.id, id));
}
