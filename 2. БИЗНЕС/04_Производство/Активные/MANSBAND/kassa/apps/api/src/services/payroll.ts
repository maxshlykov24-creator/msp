import { desc } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, payrollSettings } from "../db/schema.js";
import { DEFAULT_PAYROLL_SETTINGS } from "@kassa/shared";
import type {
  Deal,
  PayrollDay,
  PayrollReport,
  PayrollRow,
  PayrollSettings,
  PayrollTier,
} from "@kassa/shared";
import { summary } from "./stats.js";
import * as financeQueue from "./queue.js";
import { appendAudit, appendSystemAudit, type AuditActor } from "./audit.js";

/**
 * Автоматический расчёт ЗП и бонусов консультантов (созвон 20.08, п.5):
 * — за каждый рабочий день: max(выручка × %, обеспечительная ставка);
 * — премии за конверсию и UPT по порогам из конструктора (границы: from
 *   включительно, to исключительно — «87 ровно» и «87,001» попадают в разные разделы);
 * — условия единые для всех консультантов.
 *
 * Рабочий день — день, когда у консультанта есть хотя бы одна заявка в кассе
 * (графика смен в системе нет). День без заявок в расчёт не попадает.
 */

export async function getPayrollSettings(): Promise<PayrollSettings> {
  const rows = await db
    .select()
    .from(payrollSettings)
    .orderBy(desc(payrollSettings.createdAt))
    .limit(1);
  const row = rows[0];
  if (!row) {
    return {
      revenuePct: DEFAULT_PAYROLL_SETTINGS.revenuePct,
      dailyFloor: DEFAULT_PAYROLL_SETTINGS.dailyFloor,
      conversionTiers: [...DEFAULT_PAYROLL_SETTINGS.conversionTiers],
      uptTiers: [...DEFAULT_PAYROLL_SETTINGS.uptTiers],
    };
  }
  return {
    revenuePct: Number(row.revenuePct),
    dailyFloor: row.dailyFloor / 100,
    conversionTiers: (row.conversionTiers as PayrollTier[]) ?? [],
    uptTiers: (row.uptTiers as PayrollTier[]) ?? [],
    updatedBy: row.createdBy,
    updatedAt: row.createdAt.toISOString(),
  };
}

export async function savePayrollSettings(
  input: {
    revenuePct: number;
    dailyFloor: number;
    conversionTiers: PayrollTier[];
    uptTiers: PayrollTier[];
  },
  actor: AuditActor
): Promise<PayrollSettings> {
  const before = await getPayrollSettings();
  await db.insert(payrollSettings).values({
    revenuePct: String(input.revenuePct),
    dailyFloor: Math.round(input.dailyFloor * 100),
    conversionTiers: input.conversionTiers,
    uptTiers: input.uptTiers,
    createdBy: actor.name,
  });
  const after = await getPayrollSettings();
  await appendAudit({
    actor,
    action: "payroll.settings_updated",
    entityType: "payroll",
    entityId: "settings",
    before,
    after,
  });
  return after;
}

/** Порог: from включительно, to исключительно (null = без верхней границы). */
function tierBonus(tiers: PayrollTier[], value: number | null): number {
  if (value == null) return 0;
  for (const tier of tiers) {
    if (value >= tier.from && (tier.to == null || value < tier.to)) return tier.bonus;
  }
  return 0;
}

export async function payrollReport(from: string, to: string): Promise<PayrollReport> {
  const settings = await getPayrollSettings();
  const rows = await db.select().from(dealsTable);
  const all = rows
    .map((r) => r.data as Deal)
    .filter((d) => {
      const day = (d.createdAt ?? "").slice(0, 10);
      return day >= from && day <= to;
    });

  // Выручка по дням: успешные заявки консультанта, день = дата создания заявки.
  const revenueByConsultantDay = new Map<string, Map<string, number>>();
  const workDays = new Map<string, Set<string>>();
  for (const d of all) {
    const consultant = d.consultant?.trim();
    if (!consultant) continue;
    const day = (d.createdAt ?? "").slice(0, 10);
    if (!day) continue;
    const days = workDays.get(consultant) ?? new Set<string>();
    days.add(day);
    workDays.set(consultant, days);
    if (d.stage !== "Успех") continue;
    const byDay = revenueByConsultantDay.get(consultant) ?? new Map<string, number>();
    byDay.set(day, (byDay.get(day) ?? 0) + Math.max(0, d.total ?? 0));
    revenueByConsultantDay.set(consultant, byDay);
  }

  // Конверсия и UPT за период — те же формулы, что в статистике (stats.summary).
  const stats = await summary({ from, to });
  const statsByName = new Map(stats.consultants.map((r) => [r.name, r]));

  const reportRows: PayrollRow[] = [];
  for (const [consultant, days] of [...workDays.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], "ru")
  )) {
    const byDay = revenueByConsultantDay.get(consultant) ?? new Map<string, number>();
    const dayRows: PayrollDay[] = [...days]
      .sort()
      .map((date) => {
        const revenue = byDay.get(date) ?? 0;
        const pctAmount = Math.round((revenue * settings.revenuePct) / 100);
        const payout = Math.max(pctAmount, settings.dailyFloor);
        return { date, revenue, pctAmount, payout, floorApplied: payout > pctAmount };
      });
    const basePay = dayRows.reduce((s, d) => s + d.payout, 0);
    const stat = statsByName.get(consultant);
    const conversionPct = stat?.conversion != null ? stat.conversion * 100 : null;
    const upt = stat?.upt ?? null;
    const conversionBonus = tierBonus(settings.conversionTiers, conversionPct);
    const uptBonus = tierBonus(settings.uptTiers, upt);
    reportRows.push({
      consultant,
      days: dayRows,
      basePay,
      conversionPct,
      conversionBonus,
      upt,
      uptBonus,
      total: basePay + conversionBonus + uptBonus,
    });
  }

  return { from, to, settings, rows: reportRows };
}

// ── Еженедельная задача Эдвину «Выдать зарплату» ─────────────────────

function moscowToday(): Date {
  const s = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  return new Date(`${s}T12:00:00Z`);
}

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Прошлая календарная неделя (пн–вс) относительно сегодняшнего дня по МСК. */
export function lastWeekRange(): { from: string; to: string } {
  const today = moscowToday();
  const dow = today.getUTCDay() === 0 ? 7 : today.getUTCDay(); // 1 = пн
  const monday = new Date(today);
  monday.setUTCDate(monday.getUTCDate() - dow + 1 - 7);
  const sunday = new Date(monday);
  sunday.setUTCDate(sunday.getUTCDate() + 6);
  return { from: ymd(monday), to: ymd(sunday) };
}

/**
 * По вторникам ставит Эдвину задачу «Выдать зарплату» за прошлую неделю.
 * Идемпотентно: addQueue дедуплицирует по kind + dealNumber + amount + destination,
 * dealNumber = 0, destination = период недели.
 */
export async function enqueueWeeklySalary(): Promise<boolean> {
  const today = moscowToday();
  const dow = today.getUTCDay();
  if (dow !== 2) return false; // вторник

  const { from, to } = lastWeekRange();
  const report = await payrollReport(from, to);
  const total = report.rows.reduce((s, r) => s + r.total, 0);
  if (report.rows.length === 0) return false;

  const created = await financeQueue.addQueue({
    kind: "salary",
    dealNumber: 0,
    client: "Консультанты",
    amount: total,
    destination: `ЗП ${from} — ${to}`,
    metadata: {
      source: "payroll",
      from,
      to,
      rows: report.rows.map((r) => ({
        consultant: r.consultant,
        basePay: r.basePay,
        conversionPct: r.conversionPct,
        conversionBonus: r.conversionBonus,
        upt: r.upt,
        uptBonus: r.uptBonus,
        total: r.total,
        days: r.days,
      })),
      settings: report.settings,
    },
  });
  if (created) {
    await appendSystemAudit({
      action: "payroll.weekly_enqueued",
      entityType: "queue",
      entityId: created.id,
      after: { from, to, total, consultants: report.rows.length },
    });
  }
  return Boolean(created);
}
