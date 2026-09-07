import { and, gte, lte } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, expenses as expensesTable, products } from "../db/schema.js";
import { countSuitsInLines } from "@kassa/shared";
import type { Deal, SuitPart } from "@kassa/shared";
import { suitPartsByMsIds } from "./catalog.js";

/**
 * Статистика консультантов и магазинов по формулам Миши (созвон 20.08):
 * — средний чек = выручка / общее количество клиентов;
 * — конверсия = успехи / (клиенты − не-сливы − сливы, добитые в другом магазине);
 * — UPT = позиции в чеках / количество чеков;
 * — счётчики сливов и не-сливов.
 * Плюс блок для закупа: позиции по товарным группам, доходы/расходы,
 * разрезы по каналу продаж и цели покупки.
 */

export interface StatsRow {
  name: string;
  /** Все заявки за период (общее количество клиентов). */
  clients: number;
  success: number;
  revenue: number;
  avgCheck: number;
  /** null — знаменатель нулевой, конверсию не посчитать. */
  conversion: number | null;
  upt: number | null;
  slivs: number;
  noSlivs: number;
  /** Сливы с телефоном, успешные в другом магазине (вычтены из знаменателя). */
  rescuedSlivs: number;
}

export interface PurchasingStats {
  revenue: number;
  expensesTotal: number;
  expensesByCategory: Record<string, number>;
  /** Проданные позиции по товарным группам МойСклад. */
  positionsByGroup: Array<{ group: string; qty: number; revenue: number }>;
  byChannel: Record<string, number>;
  byPurpose: Record<string, number>;
}

export interface StatsSummary {
  from?: string;
  to?: string;
  totals: StatsRow;
  consultants: StatsRow[];
  stores: StatsRow[];
  purchasing: PurchasingStats;
}

function phoneKey(value: string | undefined | null): string {
  return (value ?? "").replace(/\D/g, "").slice(-10);
}

interface Bucket {
  name: string;
  deals: Deal[];
}

/** Часть костюма и вариация по msId — костюм в UPT считается одной единицей. */
type SuitInfo = Map<string, { part: SuitPart; variation: string }>;

function buildRow(
  bucket: Bucket,
  rescuedPhones: Map<string, Set<string>>,
  suitInfo: SuitInfo
): StatsRow {
  const all = bucket.deals;
  const success = all.filter((d) => d.stage === "Успех");
  const slivDeals = all.filter((d) => d.kind === "sliv");
  const noSlivs = all.filter((d) => d.kind === "no_sliv").length;
  const revenue = success.reduce((acc, d) => acc + (d.total ?? 0), 0);

  // Слив «добит» в другом магазине: телефон слива встречается в успешной
  // заявке другого магазина. Такие сливы не считаются недоработкой — вычитаем.
  let rescued = 0;
  for (const s of slivDeals) {
    const key = phoneKey(s.clientPhone);
    if (!key) continue;
    const stores = rescuedPhones.get(key);
    if (stores && [...stores].some((store) => store !== s.store)) rescued += 1;
  }

  const denominator = all.length - noSlivs - rescued;
  // UPT: костюм — одна единица, а не три (решение владельца 07.09). Иначе
  // пороги премий и штрафов по UPT берутся сами собой на любой продаже костюма.
  const itemsCount = success.reduce(
    (acc, d) =>
      acc +
      countSuitsInLines(
        d.items.map((i) => ({
          qty: Math.max(0, i.qty || 0),
          part: suitInfo.get(i.productId)?.part ?? null,
          variation: suitInfo.get(i.productId)?.variation ?? null,
        }))
      ).units,
    0
  );

  return {
    name: bucket.name,
    clients: all.length,
    success: success.length,
    revenue,
    avgCheck: all.length ? Math.round(revenue / all.length) : 0,
    conversion: denominator > 0 ? success.length / denominator : null,
    upt: success.length ? itemsCount / success.length : null,
    slivs: slivDeals.length,
    noSlivs,
    rescuedSlivs: rescued,
  };
}

/**
 * Кто смотрит статистику. Консультант видит только свои заявки (созвон 04.09):
 * сравнение с коллегами — решение владельца, заложено во вторую волну.
 */
export interface StatsViewer {
  name: string;
  role: string;
}

const EMPTY_PURCHASING: PurchasingStats = {
  revenue: 0,
  expensesTotal: 0,
  expensesByCategory: {},
  positionsByGroup: [],
  byChannel: {},
  byPurpose: {},
};

/** Период — включительно, границы в формате YYYY-MM-DD по дате создания заявки. */
export async function summary(
  period?: { from?: string; to?: string },
  viewer?: StatsViewer
): Promise<StatsSummary> {
  const rows = await db.select().from(dealsTable);
  const everything = rows.map((r) => r.data as Deal);
  // Консультанту — только его строка: и в плитках, и в разрезах.
  const ownOnly = viewer?.role === "consultant";
  const viewerName = viewer?.name?.trim() ?? "";
  const all = everything.filter((d) => {
    const day = (d.createdAt ?? "").slice(0, 10);
    if (period?.from && day < period.from) return false;
    if (period?.to && day > period.to) return false;
    if (ownOnly && (d.consultant?.trim() ?? "") !== viewerName) return false;
    return true;
  });

  // Телефоны успешных заявок по всей базе (не только период): слив мог быть
  // добит другим магазином позже границы периода.
  const rescuedPhones = new Map<string, Set<string>>();
  for (const d of everything) {
    if (d.stage !== "Успех") continue;
    const key = phoneKey(d.clientPhone);
    if (!key) continue;
    const set = rescuedPhones.get(key) ?? new Set<string>();
    set.add(d.store);
    rescuedPhones.set(key, set);
  }

  const byConsultant = new Map<string, Deal[]>();
  const byStore = new Map<string, Deal[]>();
  for (const d of all) {
    const consultant = d.consultant?.trim() || "—";
    byConsultant.set(consultant, [...(byConsultant.get(consultant) ?? []), d]);
    byStore.set(d.store, [...(byStore.get(d.store) ?? []), d]);
  }

  const suitInfo = await suitPartsByMsIds(
    all.flatMap((d) => d.items.map((i) => i.productId).filter(Boolean))
  );

  const consultants = [...byConsultant.entries()]
    .map(([name, deals]) => buildRow({ name, deals }, rescuedPhones, suitInfo))
    .sort((a, b) => b.revenue - a.revenue);
  const stores = [...byStore.entries()]
    .map(([name, deals]) => buildRow({ name, deals }, rescuedPhones, suitInfo))
    .sort((a, b) => b.revenue - a.revenue);
  const totals = buildRow({ name: "Все", deals: all }, rescuedPhones, suitInfo);

  // Закуп и расходы Эдвина — не зона консультанта.
  const purchasing = ownOnly ? EMPTY_PURCHASING : await purchasingStats(all, period);

  return { from: period?.from, to: period?.to, totals, consultants, stores, purchasing };
}

/** Блок для закупа и планирования: «сколько костюмов продано» без ручного счёта. */
async function purchasingStats(
  all: Deal[],
  period?: { from?: string; to?: string }
): Promise<PurchasingStats> {
  const success = all.filter((d) => d.stage === "Успех");
  const revenue = success.reduce((acc, d) => acc + (d.total ?? 0), 0);

  // Категория позиции — из кэша каталога МойСклад по productId (ms id).
  const productRows = await db
    .select({ msId: products.msId, category: products.category })
    .from(products);
  const categoryByMsId = new Map(productRows.map((p) => [p.msId, p.category ?? ""]));

  const groups = new Map<string, { qty: number; revenue: number }>();
  for (const d of success) {
    for (const item of d.items) {
      if (!item.productId || item.isReturn) continue;
      const group = categoryByMsId.get(item.productId)?.trim() || "Без группы";
      const g = groups.get(group) ?? { qty: 0, revenue: 0 };
      g.qty += Math.max(0, item.qty || 0);
      g.revenue += Math.max(0, (item.price || 0) * (item.qty || 0));
      groups.set(group, g);
    }
  }
  const positionsByGroup = [...groups.entries()]
    .map(([group, v]) => ({ group, ...v }))
    .sort((a, b) => b.qty - a.qty);

  const byChannel: Record<string, number> = {};
  const byPurpose: Record<string, number> = {};
  for (const d of success) {
    if (d.channel) byChannel[d.channel] = (byChannel[d.channel] ?? 0) + 1;
    if (d.purpose) byPurpose[d.purpose] = (byPurpose[d.purpose] ?? 0) + 1;
  }

  // Расходы Эдвина за период (spent_at, суммы в копейках → рубли).
  const conditions = [
    period?.from ? gte(expensesTable.spentAt, new Date(`${period.from}T00:00:00+03:00`)) : undefined,
    period?.to ? lte(expensesTable.spentAt, new Date(`${period.to}T23:59:59+03:00`)) : undefined,
  ].filter((c): c is NonNullable<typeof c> => Boolean(c));
  const expenseRows = await db
    .select()
    .from(expensesTable)
    .where(conditions.length > 0 ? and(...conditions) : undefined);
  const expensesByCategory: Record<string, number> = {};
  let expensesTotal = 0;
  for (const e of expenseRows) {
    const rub = e.amount / 100;
    expensesTotal += rub;
    expensesByCategory[e.category] = (expensesByCategory[e.category] ?? 0) + rub;
  }

  return { revenue, expensesTotal, expensesByCategory, positionsByGroup, byChannel, byPurpose };
}
