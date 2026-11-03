import type { CartItem, Deal } from "./types.js";

/** Та же скидка на единицу и округление до рубля, что применяются в форме товара. */
export function cartLineTotal(item: CartItem): number {
  if (item.noPrice || item.isGift) return 0;
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
  const changed = patch.items !== undefined || patch.atelierAmount !== undefined || patch.deliveryAmount !== undefined || patch.saryBonus !== undefined;
  const total = changed
    ? signedItemsTotal(merged.items) + adjustment + fees(merged) - fees(existing) - (merged.saryBonus ?? 0) + (existing.saryBonus ?? 0)
    : existing.total;
  return {
    total: Math.round((existing.kind === "refund" || existing.kind === "exchange" ? total : Math.max(0, total)) * 100) / 100,
    paid: patch.payments ? Math.round(patch.payments.reduce((sum, payment) => sum + payment.amount, 0) * 100) / 100 : existing.paid,
  };
}
