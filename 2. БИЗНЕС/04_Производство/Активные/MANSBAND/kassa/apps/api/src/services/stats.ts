import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, expenses as expensesTable, products, shifts, ledger } from "../db/schema.js";
import { countSuitsInLines, isTestDeal, isSalesVisit, dealAccountingDay, cartLineTotal, settledItems } from "@kassa/shared";
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
  channels: StatsRow[];
  callManagers: StatsRow[];
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
  const visits = all.filter(isSalesVisit);
  const success = visits.filter((d) => d.stage === "Успех");
  const slivDeals = all.filter((d) => d.kind === "sliv");
  const noSlivs = all.filter((d) => d.kind === "no_sliv").length;
  const revenue = all.filter(d => d.stage === "Успех" && (isSalesVisit(d) || ["refund", "exchange"].includes(d.kind))).reduce((acc, d) => acc + (d.total ?? 0), 0);

  // Слив «добит» в другом магазине: телефон слива встречается в успешной
  // заявке другого магазина. Такие сливы не считаются недоработкой — вычитаем.
  let rescued = 0;
  for (const s of slivDeals) {
    const key = phoneKey(s.clientPhone);
    if (!key) continue;
    const stores = rescuedPhones.get(key);
    if (stores && [...stores].some((store) => store !== s.store)) rescued += 1;
  }

  const denominator = visits.length - noSlivs - rescued;
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
    clients: visits.length,
    success: success.length,
    revenue,
    avgCheck: visits.length ? Math.round(revenue / visits.length) : 0,
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
  const originals = rows.map((r) => r.data as Deal);
  const everything = originals.filter(d => !isTestDeal(d)).map(d => {
    if (!["refund", "exchange"].includes(d.kind) || !d.linkedDealNumber) return d;
    const original = originals.find(s => s.number === d.linkedDealNumber || s.amoLeadId === d.linkedDealNumber);
    return original ? { ...d, consultant: original.consultant, callManager: original.callManager, funnel: original.funnel } : d;
  });
  // Консультанту — только его строка: и в плитках, и в разрезах.
  const ownOnly = viewer?.role === "consultant";
  const viewerName = viewer?.name?.trim() ?? "";
  const all = everything.filter((d) => {
    const day = dealAccountingDay(d);
    if (period?.from && day < period.from) return false;
    if (period?.to && day > period.to) return false;
    if (ownOnly && (d.consultant?.trim() ?? "") !== viewerName) return false;
    return true;
  });

  // Телефоны успешных заявок по всей базе (не только период): слив мог быть
  // добит другим магазином позже границы периода.
  const rescuedPhones = new Map<string, Set<string>>();
  for (const d of everything) {
    if (d.stage !== "Успех" || !isSalesVisit(d)) continue;
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

  const channels = ["offline", "online"].map(funnel => buildRow({ name: funnel === "online" ? "Онлайн" : "Магазины", deals: all.filter(d => d.funnel === funnel) }, rescuedPhones, suitInfo));
  const names = [...new Set(all.filter(d => d.funnel === "online").map(d => d.callManager || "Не назначен"))];
  const callManagers = names.map(name => buildRow({ name, deals: all.filter(d => d.funnel === "online" && (d.callManager || "Не назначен") === name) }, rescuedPhones, suitInfo));
  return { from: period?.from, to: period?.to, totals, consultants, stores, purchasing, channels, callManagers };
}

/** Блок для закупа и планирования: «сколько костюмов продано» без ручного счёта. */
async function purchasingStats(
  all: Deal[],
  period?: { from?: string; to?: string }
): Promise<PurchasingStats> {
  const success = all.filter((d) => d.stage === "Успех" && (isSalesVisit(d) || ["refund", "exchange"].includes(d.kind)));
  const revenue = success.reduce((acc, d) => acc + (d.total ?? 0), 0);

  // Категория позиции — из кэша каталога МойСклад по productId (ms id).
  const productRows = await db
    .select({ msId: products.msId, category: products.category })
    .from(products);
  const categoryByMsId = new Map(productRows.map((p) => [p.msId, p.category ?? ""]));

  const groups = new Map<string, { qty: number; revenue: number }>();
  for (const d of success) {
    const positions = d.kind === "refund" || d.kind === "exchange" ? d.items : settledItems(d);
    for (const item of positions) {
      if (!item.productId) continue;
      const sign = d.kind === "refund" || item.isReturn ? -1 : 1;
      const group = categoryByMsId.get(item.productId)?.trim() || "Без группы";
      const g = groups.get(group) ?? { qty: 0, revenue: 0 };
      g.qty += sign * Math.max(0, item.qty || 0);
      g.revenue += sign * cartLineTotal(item);
      groups.set(group, g);
    }
  }
  const positionsByGroup = [...groups.entries()]
    .map(([group, v]) => ({ group, ...v }))
    .sort((a, b) => b.qty - a.qty);

  const byChannel: Record<string, number> = {};
  const byPurpose: Record<string, number> = {};
  for (const d of success.filter(isSalesVisit)) {
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


/** Сверка физической наличности магазина. Остаток переносится только из подтверждённой сверки. */
export async function cashShift(store: string, cutoff = new Date().toISOString()) {
  const [previous] = await db.select().from(shifts).where(and(eq(shifts.store,store),sql`${shifts.payload}->>'kind' = 'cash_close'`)).orderBy(desc(shifts.closedAt)).limit(1);
  const from = previous?.closedAt?.toISOString() ?? `${new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Moscow"}).format(new Date(cutoff))}T00:00:00+03:00`;
  const movements = await db.select({amount:ledger.amount,deal:dealsTable.data}).from(ledger).innerJoin(dealsTable,eq(dealsTable.number,ledger.dealNumber))
    .where(and(sql`${dealsTable.data}->>'store' = ${store}`,sql`${ledger.kind} in ('cash','cash_out')`,sql`${ledger.createdAt} > ${from}::timestamptz and ${ledger.createdAt} <= ${cutoff}::timestamptz`));
  return {store,from,cutoff,previousId:previous?.id??null,openingCash:previous?.countedCash==null?null:previous.countedCash/100,movement:movements.filter(row=>!isTestDeal(row.deal as Deal)).reduce((sum,row)=>sum+row.amount,0)/100,operations:movements.filter(row=>!isTestDeal(row.deal as Deal)).length};
}
export async function closeCashShift(input: {store:string;cutoff:string;previousId:string|null;openingCash:number;countedCash:number;adjustment:number;reason:string}, actor: {name:string;role:string}) {
  return db.transaction(async tx=>{
    await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${"cash-close:"+input.store},717))`);
    const current=await cashShift(input.store,input.cutoff);
    if(new Date(input.cutoff).valueOf()<=new Date(current.from).valueOf())throw Object.assign(new Error("Период сверки уже закрыт"),{statusCode:409});
    if(current.previousId!==input.previousId)throw Object.assign(new Error("Смена уже сверена другим сотрудником. Обновите данные."),{statusCode:409});
    if(current.openingCash===null && !["admin","rop"].includes(actor.role))throw Object.assign(new Error("Первый остаток наличных подтверждает руководитель"),{statusCode:403});
    const opening=current.openingCash??input.openingCash;
    const expected=Math.round((opening+current.movement+input.adjustment)*100);
    const counted=Math.round(input.countedCash*100);
    if(expected!==counted)throw Object.assign(new Error("Фактическая наличность не совпадает с расчётной. Проверьте суммы и движения."),{statusCode:409});
    if((input.adjustment!==0||current.openingCash===null)&&input.reason.trim().length<3)throw Object.assign(new Error("Укажите основание начального остатка или прочих движений"),{statusCode:400});
    const [row]=await tx.insert(shifts).values({store:input.store,openedBy:actor.name,openedAt:new Date(current.from),closedAt:new Date(input.cutoff),expectedCash:expected,countedCash:counted,reconciled:true,payload:{kind:"cash_close",openingCash:opening,movement:current.movement,operations:current.operations,adjustment:input.adjustment,reason:input.reason,confirmedBy:actor.name}}).returning();
    return {id:row!.id,closedAt:input.cutoff,expectedCash:expected/100,countedCash:counted/100,reconciled:true};
  });
}
