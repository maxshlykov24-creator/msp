import { and, desc, eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { auditLog } from "../db/schema.js";
import type { AuditEntry, AuditSource, UserRole } from "@kassa/shared";

export interface AuditActor {
  id?: string;
  name: string;
  role: UserRole | string;
}

/** Системный актор для автоматических событий (формула задач, синк, cron). */
export const SYSTEM_AUDIT_ACTOR: AuditActor = { name: "Касса (авто)", role: "system" };

export async function appendAudit(input: {
  actor: AuditActor;
  action: string;
  entityType: string;
  entityId: string;
  /** Не задан — manual для людей, auto для системного актора. */
  source?: AuditSource;
  before?: unknown;
  after?: unknown;
  metadata?: Record<string, unknown>;
}): Promise<void> {
  const source: AuditSource =
    input.source ?? (input.actor.name === SYSTEM_AUDIT_ACTOR.name ? "auto" : "manual");
  await db.insert(auditLog).values({
    actorId: input.actor.id ?? null,
    actorName: input.actor.name,
    actorRole: input.actor.role,
    action: input.action,
    entityType: input.entityType,
    entityId: input.entityId,
    source,
    before: input.before as object | undefined,
    after: input.after as object | undefined,
    metadata: input.metadata,
  });
}

/**
 * Аудит системного события («любой пук — в историю», созвон 20.08).
 * Ошибка записи не должна ломать бизнес-операцию — глотаем с логом.
 */
export async function appendSystemAudit(input: {
  action: string;
  entityType: string;
  entityId: string;
  before?: unknown;
  after?: unknown;
  metadata?: Record<string, unknown>;
}): Promise<void> {
  try {
    await appendAudit({ actor: SYSTEM_AUDIT_ACTOR, source: "auto", ...input });
  } catch (err) {
    console.warn(`[audit] системное событие ${input.action} не записано: ${(err as Error).message}`);
  }
}

function map(row: typeof auditLog.$inferSelect): AuditEntry {
  return {
    id: row.id,
    actorId: row.actorId ?? undefined,
    actorName: row.actorName,
    actorRole: row.actorRole as UserRole,
    action: row.action,
    entityType: row.entityType,
    entityId: row.entityId,
    source: (row.source as AuditSource) ?? "manual",
    before: row.before ?? undefined,
    after: row.after ?? undefined,
    metadata: (row.metadata as Record<string, unknown> | null) ?? undefined,
    createdAt: row.createdAt.toISOString(),
  };
}

export async function listAudit(args: {
  entityType?: string;
  entityId?: string;
  source?: AuditSource;
  limit?: number;
}): Promise<AuditEntry[]> {
  const limit = Math.min(Math.max(args.limit ?? 100, 1), 500);
  const conditions = [
    args.entityType ? eq(auditLog.entityType, args.entityType) : undefined,
    args.entityType && args.entityId ? eq(auditLog.entityId, args.entityId) : undefined,
    args.source ? eq(auditLog.source, args.source) : undefined,
  ].filter((c): c is NonNullable<typeof c> => Boolean(c));
  const where = conditions.length > 0 ? and(...conditions) : undefined;
  const rows = await db.select().from(auditLog).where(where).orderBy(desc(auditLog.createdAt)).limit(limit);
  return rows.map(map);
}
