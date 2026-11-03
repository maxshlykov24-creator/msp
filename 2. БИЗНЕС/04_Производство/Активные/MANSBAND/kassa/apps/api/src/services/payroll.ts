import { withOperationLock } from "../lib/idempotency.js";
import { and, desc, eq, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, payrollSettings, shifts, queue } from "../db/schema.js";
import { DEFAULT_PAYROLL_SETTINGS, isTestDeal, isSalesVisit, dealAccountingDay } from "@kassa/shared";
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
 * Рабочие дни подтверждает руководитель. Заявки дают предварительные дни;
 * выплата до подтверждения блокируется. Подтверждённые дни без заявок учитываются.
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
      penaltyConversionTiers: [...DEFAULT_PAYROLL_SETTINGS.penaltyConversionTiers],
      penaltyUptTiers: [...DEFAULT_PAYROLL_SETTINGS.penaltyUptTiers],
    };
  }
  return {
    revenuePct: Number(row.revenuePct),
    dailyFloor: row.dailyFloor / 100,
    conversionTiers: (row.conversionTiers as PayrollTier[]) ?? [],
    uptTiers: (row.uptTiers as PayrollTier[]) ?? [],
    penaltyConversionTiers: (row.penaltyConversionTiers as PayrollTier[]) ?? [],
    penaltyUptTiers: (row.penaltyUptTiers as PayrollTier[]) ?? [],
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
    penaltyConversionTiers?: PayrollTier[];
    penaltyUptTiers?: PayrollTier[];
  },
  actor: AuditActor
): Promise<PayrollSettings> {
  const before = await getPayrollSettings();
  await db.insert(payrollSettings).values({
    revenuePct: String(input.revenuePct),
    dailyFloor: Math.round(input.dailyFloor * 100),
    conversionTiers: input.conversionTiers,
    uptTiers: input.uptTiers,
    penaltyConversionTiers: input.penaltyConversionTiers ?? [],
    penaltyUptTiers: input.penaltyUptTiers ?? [],
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

/**
 * Процент премии или штрафа по порогам: from включительно, to исключительно
 * (null = без верхней границы). Ноль — ни один порог не подошёл.
 */
function tierPct(tiers: PayrollTier[], value: number | null): number {
  if (value == null) return 0;
  for (const tier of tiers) {
    if (value >= tier.from && (tier.to == null || value < tier.to)) return tier.bonusPct;
  }
  return 0;
}

/** Премия или штраф в рублях: процент от выручки консультанта за период. */
function pctOf(revenue: number, pct: number): number {
  if (!pct) return 0;
  return Math.round((revenue * pct) / 100);
}

export async function payrollReport(from: string, to: string): Promise<PayrollReport> {
  const settings = await getPayrollSettings();
  const rows = await db.select().from(dealsTable);
  const originals = rows.map(r => r.data as Deal);
  const all = originals.map(d => {
    if (!["refund", "exchange"].includes(d.kind) || !d.linkedDealNumber) return d;
    const source = originals.find(s => s.number === d.linkedDealNumber || s.amoLeadId === d.linkedDealNumber);
    return source ? { ...d, consultant: source.consultant } : d;
  })
    .filter((d) => {
      if (isTestDeal(d) || d.funnel === "defects") return false;
      const day = dealAccountingDay(d);
      return day >= from && day <= to;
    });

  // Выручка по дням завершения; старые записи без истории используют дату создания.
  const revenueByConsultantDay = new Map<string, Map<string, number>>();
  const workDays = new Map<string, Set<string>>();
  for (const d of all) {
    const consultant = d.consultant?.trim();
    if (!consultant) continue;
    const day = dealAccountingDay(d);
    if (!day) continue;
    const days = workDays.get(consultant) ?? new Set<string>();
    if (isSalesVisit(d)) days.add(day);
    workDays.set(consultant, days);
    if (d.stage !== "Успех") continue;
    const byDay = revenueByConsultantDay.get(consultant) ?? new Map<string, number>();
    byDay.set(day, (byDay.get(day) ?? 0) + (d.total ?? 0));
    revenueByConsultantDay.set(consultant, byDay);
  }

  const confirmed = await listWorkdays(from, to);
  const warnings: string[] = [];
  for (const [name, days] of workDays) {
    for (const date of days) if (!confirmed.some(row => row.consultant === name && row.date === date)) warnings.push(`${name}: не подтверждён рабочий день ${date}`);
  }
  for (const row of confirmed) {
    const days = workDays.get(row.consultant) ?? new Set<string>();
    if (row.active) days.add(row.date);
    else days.delete(row.date);
    workDays.set(row.consultant, days);
  }

  // Конверсия и UPT за период — те же формулы, что в статистике (stats.summary).
  const stats = await summary({ from, to });
  const statsByName = new Map(stats.consultants.map((r) => [r.name, r]));

  const reportRows: PayrollRow[] = [];
  for (const [consultant, days] of [...workDays.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], "ru")
  )) {
    const byDay = revenueByConsultantDay.get(consultant) ?? new Map<string, number>();
    const dayRows: PayrollDay[] = [...new Set([...days, ...byDay.keys()])]
      .sort()
      .map((date) => {
        const revenue = byDay.get(date) ?? 0;
        const pctAmount = Math.round((revenue * settings.revenuePct) / 100);
        const payout = days.has(date) ? Math.max(pctAmount, settings.dailyFloor) : pctAmount;
        return { date, revenue, pctAmount, payout, floorApplied: payout > pctAmount };
      });
    const basePay = dayRows.reduce((s, d) => s + d.payout, 0);
    const periodRevenue = dayRows.reduce((s, d) => s + d.revenue, 0);
    const stat = statsByName.get(consultant);
    const conversionPct = stat?.conversion != null ? stat.conversion * 100 : null;
    const upt = stat?.upt ?? null;
    // Премии и штрафы — проценты от выручки за период (созвон 04.09).
    const conversionBonusPct = tierPct(settings.conversionTiers, conversionPct);
    const uptBonusPct = tierPct(settings.uptTiers, upt);
    const conversionPenaltyPct = tierPct(settings.penaltyConversionTiers, conversionPct);
    const uptPenaltyPct = tierPct(settings.penaltyUptTiers, upt);
    const conversionBonus = pctOf(Math.max(0, periodRevenue), conversionBonusPct);
    const uptBonus = pctOf(Math.max(0, periodRevenue), uptBonusPct);
    const conversionPenalty = pctOf(Math.max(0, periodRevenue), conversionPenaltyPct);
    const uptPenalty = pctOf(Math.max(0, periodRevenue), uptPenaltyPct);
    reportRows.push({
      consultant,
      days: dayRows,
      basePay,
      periodRevenue,
      conversionPct,
      conversionBonusPct,
      conversionBonus,
      upt,
      uptBonusPct,
      uptBonus,
      conversionPenaltyPct,
      conversionPenalty,
      uptPenaltyPct,
      uptPenalty,
      // Штраф не может увести выплату в минус.
      total: Math.max(
        0,
        basePay + conversionBonus + uptBonus - conversionPenalty - uptPenalty
      ),
    });
  }

  return { from, to, settings, rows: reportRows, warnings };
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
 * Ставит Эдвину задачу «Выдать зарплату» за период. Идемпотентно: addQueue
 * дедуплицирует по kind + dealNumber + amount + destination, dealNumber = 0,
 * destination = период. Владелец 04.09 попросил ручной расчёт за любой период,
 * поэтому один и тот же код обслуживает и вторничную автоматику, и кнопку РОПа.
 */
async function enqueueSalaryForPeriodUnlocked(
  from: string,
  to: string,
  who = "Касса (авто)"
): Promise<{ created: boolean; total: number; consultants: number }> {
  const report = await payrollReport(from, to);
  if (report.warnings?.length) throw Object.assign(new Error("Перед выплатой подтвердите рабочие дни в настройках: " + report.warnings.join("; ")), { statusCode: 409 });
  const total = report.rows.reduce((s, r) => s + r.total, 0);
  if (report.rows.length === 0 || total <= 0) return { created: false, total: 0, consultants: report.rows.length };

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
      createdBy: who,
      rows: report.rows.map((r) => ({
        consultant: r.consultant,
        basePay: r.basePay,
        periodRevenue: r.periodRevenue,
        conversionPct: r.conversionPct,
        conversionBonusPct: r.conversionBonusPct,
        conversionBonus: r.conversionBonus,
        upt: r.upt,
        uptBonusPct: r.uptBonusPct,
        uptBonus: r.uptBonus,
        conversionPenaltyPct: r.conversionPenaltyPct,
        conversionPenalty: r.conversionPenalty,
        uptPenaltyPct: r.uptPenaltyPct,
        uptPenalty: r.uptPenalty,
        total: r.total,
        days: r.days,
      })),
      settings: report.settings,
    },
  });
  if (created) {
    await appendSystemAudit({
      action: "payroll.enqueued",
      entityType: "queue",
      entityId: created.id,
      after: { from, to, total, consultants: report.rows.length, by: who },
    });
  }
  return { created: Boolean(created), total, consultants: report.rows.length };
}

/** По вторникам — задача Эдвину за прошлую календарную неделю. */
export async function enqueueWeeklySalary(): Promise<boolean> {
  const today = moscowToday();
  if (today.getUTCDay() !== 2) return false; // вторник

  const { from, to } = lastWeekRange();
  const res = await enqueueSalaryForPeriod(from, to);
  return res.created;
}


export interface Workday { consultant: string; date: string; store: string; active: boolean; confirmedBy: string }
export async function listWorkdays(from: string, to: string): Promise<Workday[]> {
  const rows = await db.select().from(shifts).where(sql`${shifts.payload}->>'kind' = 'workday' and ${shifts.payload}->>'date' >= ${from} and ${shifts.payload}->>'date' <= ${to}`);
  return rows.map(row => {
    const value = row.payload as { date: string; active: boolean; confirmedBy: string };
    return { consultant: row.openedBy, store: row.store, date: value.date, active: value.active, confirmedBy: value.confirmedBy };
  }).sort((a,b) => a.date.localeCompare(b.date) || a.consultant.localeCompare(b.consultant, "ru"));
}
export async function confirmWorkday(input: Omit<Workday, "confirmedBy"> & { reason: string }, actor: AuditActor): Promise<Workday> {
  const updated = await db.transaction(async tx => {
    await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${`workday:${input.consultant}:${input.date}`}, 716))`);
    const [paid] = await tx.select({id:queue.id}).from(queue).where(and(eq(queue.kind,"salary"),sql`${queue.issuedAmount} > 0 and ${queue.metadata}->>'from' <= ${input.date} and ${queue.metadata}->>'to' >= ${input.date}`)).limit(1);
    if(paid)throw Object.assign(new Error("За этот период зарплата уже выплачивалась. Нужна отдельная корректировка."),{statusCode:409});
    const [existing] = await tx.select().from(shifts).where(and(eq(shifts.openedBy, input.consultant), sql`${shifts.payload}->>'kind' = 'workday' and ${shifts.payload}->>'date' = ${input.date}`));
    const values = { store: input.store, openedBy: input.consultant, payload: {kind:"workday",date:input.date,active:input.active,confirmedBy:actor.name,reason:input.reason}, reconciled:true };
    if (existing) await tx.update(shifts).set(values).where(eq(shifts.id, existing.id));
    else await tx.insert(shifts).values(values);
    return { ...input, confirmedBy: actor.name };
  });
  await appendAudit({actor,action:"payroll.workday_confirmed",entityType:"shift",entityId:`${input.consultant}:${input.date}`,after:updated,metadata:{reason:input.reason}});
  return updated;
}


export async function enqueueSalaryForPeriod(from:string,to:string,who="Касса (авто)") {
  return withOperationLock("salary-period",async()=>{
    const [overlap]=await db.select({id:queue.id}).from(queue).where(and(eq(queue.kind,"salary"),sql`${queue.metadata}->>'from' <= ${to} and ${queue.metadata}->>'to' >= ${from} and not (${queue.metadata}->>'from' = ${from} and ${queue.metadata}->>'to' = ${to})`)).limit(1);
    if(overlap)throw Object.assign(new Error("Часть периода уже включена в другую зарплатную ведомость. Нужна сверка, повторная выплата не создана."),{statusCode:409});
    return enqueueSalaryForPeriodUnlocked(from,to,who);
  });
}
