import type { CartItem, Deal } from "./types.js";

/** Та же скидка на единицу и округление до рубля, что применяются в форме товара. */
export function cartLineTotal(item: CartItem): number {
  if (item.noPrice || item.isGift) return 0;
  if (item.settledUnitPrice != null) return Math.round(item.settledUnitPrice * 100) * item.qty / 100;
  let unit = Math.abs(item.price);
  if (item.discountPct) unit *= 1 - item.discountPct / 100;
  if (item.discountRub) unit -= item.discountRub;
  return Math.max(0, Math.round(unit)) * item.qty;
}

function signedItemsTotal(items: CartItem[]): number {
  return items.reduce((sum, item) => sum + cartLineTotal(item) * (item.isReturn || item.price < 0 ? -1 : 1), 0);
}

/** При редактировании меняем сумму вслед за товарами, сохраняя скидку чека и услуги. */
export function recalculateDealTotals(existing: Deal, patch: Partial<Deal>): Pick<Deal, "total" | "paid"> {
  const fees = (deal: Partial<Deal>) => (deal.atelierAmount ?? 0) + (deal.deliveryAmount ?? 0);
  const merged = { ...existing, ...patch };
  const oldGoods = signedItemsTotal(existing.items);
  const adjustment = existing.items.length
    ? existing.total - oldGoods
    : fees(existing) - (existing.checkDiscount ?? 0) - (existing.saryBonus ?? 0);
  const changed = patch.items !== undefined || patch.atelierAmount !== undefined || patch.deliveryAmount !== undefined || patch.saryBonus !== undefined || patch.checkDiscount !== undefined;
  const total = changed
    ? signedItemsTotal(merged.items) + adjustment + fees(merged) - fees(existing) - (merged.saryBonus ?? 0) + (existing.saryBonus ?? 0) - (merged.checkDiscount ?? 0) + (existing.checkDiscount ?? 0)
    : existing.total;
  return {
    total: Math.round((existing.kind === "refund" || existing.kind === "exchange" ? total : Math.max(0, total)) * 100) / 100,
    paid: patch.payments ? Math.round(patch.payments.reduce((sum, payment) => sum + payment.amount, 0) * 100) / 100 : existing.paid,
  };
}

/** Распределение скидки чека по товарам в целых копейках; услуги не возвращаем как товар. */
export function settledItems(deal: Deal): CartItem[] {
  const items = deal.items.filter(i => i.qty > 0 && !i.isReturn && i.price >= 0);
  if (deal.kind === "exchange") return items;
  const gross = items.map(i => Math.round(cartLineTotal(i) * 100));
  const sum = gross.reduce((a, b) => a + b, 0);
  const target = Math.min(sum, Math.max(0, Math.round((deal.total - (deal.atelierAmount ?? 0) - (deal.deliveryAmount ?? 0)) * 100)));
  let assigned = 0;
  let cumulative = 0;
  return items.flatMap((item, index) => {
    cumulative += gross[index]!;
    const next = sum ? Math.round(target * cumulative / sum) : 0;
    const cents = next - assigned;
    assigned = next;
    const unit = Math.floor(cents / item.qty);
    const extra = cents - unit * item.qty;
    const line = (qty: number, price: number, suffix: string): CartItem => ({
      ...item, qty, settledUnitPrice: price / 100,
      lineId: `${item.lineId ?? item.productId}:${index}:${suffix}`,
    });
    return [ ...(item.qty > extra ? [line(item.qty - extra, unit, "base")] : []),
      ...(extra ? [line(extra, unit + 1, "cent")] : []) ];
  });
}


export function isTestDeal(deal: Deal): boolean {
  return deal.isTest === true || /(^|[\s·_—-])(тест|test)(?=$|[\s·_—-])/i.test(deal.clientName ?? "");
}
export function dealAccountingDay(deal: Deal): string {
  const success = deal.completedAt ?? [...(deal.history ?? [])].reverse().find(h => /→ Успех/.test(h.action))?.at;
  const value = deal.stage === "Успех" ? success ?? deal.createdAt : deal.createdAt;
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(date) : "";
}
export function isSalesVisit(deal: Deal): boolean {
  return !["refund", "exchange", "defect", "drycleaning", "resew", "wrong_size", "wrong_label"].includes(deal.kind);
}
