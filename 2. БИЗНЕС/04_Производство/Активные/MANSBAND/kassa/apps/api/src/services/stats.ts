import { db } from "../db/index.js";
import { deals as dealsTable } from "../db/schema.js";
import type { Deal } from "@kassa/shared";

// Базовая статистика/KPI (Фаза 5): по консультанту / магазину / бренду.
// Считаем по локальному зеркалу заявок. Заказы без онлайн-участия можно исключать на фронте.

export interface KpiSummary {
  totalDeals: number;
  totalRevenue: number;
  successCount: number;
  conversion: number;
  avgCheck: number;
  byStore: Record<string, { count: number; revenue: number }>;
  byConsultant: Record<string, { count: number; revenue: number }>;
}

export async function summary(): Promise<KpiSummary> {
  const rows = await db.select().from(dealsTable);
  const all = rows.map((r) => r.data as Deal);
  const success = all.filter((d) => d.stage === "Успех");
  const revenue = success.reduce((acc, d) => acc + (d.total ?? 0), 0);

  const byStore: KpiSummary["byStore"] = {};
  const byConsultant: KpiSummary["byConsultant"] = {};
  for (const d of success) {
    const s = (byStore[d.store] ??= { count: 0, revenue: 0 });
    s.count++;
    s.revenue += d.total ?? 0;
    const key = d.consultant || "—";
    const c = (byConsultant[key] ??= { count: 0, revenue: 0 });
    c.count++;
    c.revenue += d.total ?? 0;
  }

  return {
    totalDeals: all.length,
    totalRevenue: revenue,
    successCount: success.length,
    conversion: all.length ? success.length / all.length : 0,
    avgCheck: success.length ? Math.round(revenue / success.length) : 0,
    byStore,
    byConsultant,
  };
}
