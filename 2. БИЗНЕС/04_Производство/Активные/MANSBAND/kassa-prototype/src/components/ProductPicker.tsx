import { useState } from "react";
import { Search, Plus, Minus, Trash2, ScanLine, X } from "lucide-react";
import type { CartItem } from "../data/types";
import { PRODUCTS } from "../data/mock";
import { money, normalizeSearch } from "../lib/format";

export function lineTotal(it: CartItem): number {
  if (it.noPrice) return 0;
  let unit = it.price;
  if (it.isGift) return 0;
  if (it.discountPct) unit = unit * (1 - it.discountPct / 100);
  if (it.discountRub) unit = unit - it.discountRub;
  return Math.max(0, Math.round(unit)) * it.qty;
}

export function cartTotal(items: CartItem[]): number {
  return items.reduce((s, it) => s + lineTotal(it), 0);
}

/** Сумма скидки по конкретной позиции в ₽ (для отображения). */
export function lineDiscount(it: CartItem): number {
  if (it.noPrice) return 0;
  return Math.max(0, it.price * it.qty - lineTotal(it));
}

export function ProductPicker({
  items,
  onChange,
  noPrice = false,
}: {
  items: CartItem[];
  onChange: (items: CartItem[]) => void;
  noPrice?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);

  const qNorm = normalizeSearch(q);
  const found = PRODUCTS.filter(
    (p) =>
      normalizeSearch(p.name).includes(qNorm) ||
      normalizeSearch(p.sku).includes(qNorm)
  );

  function addProduct(id: string) {
    const p = PRODUCTS.find((x) => x.id === id)!;
    const existing = items.find((i) => i.productId === id);
    if (existing) {
      onChange(items.map((i) => (i.productId === id ? { ...i, qty: i.qty + 1 } : i)));
    } else {
      onChange([
        ...items,
        { productId: id, name: p.name, price: noPrice ? 0 : p.price, qty: 1, noPrice },
      ]);
    }
    setQ("");
    setOpen(false);
  }

  function update(id: string, patch: Partial<CartItem>) {
    onChange(items.map((i) => (i.productId === id ? { ...i, ...patch } : i)));
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
            <input
              className="input pl-9"
              placeholder="Поиск товара или артикул…"
              value={q}
              onFocus={() => setOpen(true)}
              onChange={(e) => {
                setQ(e.target.value);
                setOpen(true);
              }}
            />
          </div>
          <button
            className="inline-flex items-center gap-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-white font-semibold px-3"
            title="Сканировать штрихкод"
            onClick={() => addProduct(PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)].id)}
          >
            <ScanLine size={18} />
          </button>
        </div>
        {open && q && (
          <div className="absolute z-20 mt-1 w-full card p-1 max-h-64 overflow-y-auto">
            <div className="flex justify-end px-2 pt-1">
              <button onClick={() => setOpen(false)} className="text-mute hover:text-white">
                <X size={14} />
              </button>
            </div>
            {found.length === 0 && <div className="px-3 py-3 text-mute text-sm">Ничего не найдено</div>}
            {found.map((p) => (
              <button
                key={p.id}
                onClick={() => addProduct(p.id)}
                className="w-full text-left px-3 py-2 rounded-lg hover:bg-ink-800 flex items-center gap-3"
              >
                <div className="flex-1">
                  <div className="text-[14px] text-white">{p.name}</div>
                  <div className="text-[12px] text-mute">
                    {p.sku} · {p.category} · {p.store} · ост. {p.stock}
                  </div>
                </div>
                {!noPrice && <div className="text-gold-soft font-semibold">{money(p.price)}</div>}
                <Plus size={16} className="text-gold" />
              </button>
            ))}
          </div>
        )}
      </div>

      {items.length > 0 && (
        <div className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
          {items.map((it) => (
            <div key={it.productId} className="px-3 py-2.5 bg-ink-900/50">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[14px] text-white">{it.name}</div>
                  {!it.noPrice && (
                    <div className="text-[12px] text-mute">
                      {money(it.price)} / шт
                      {it.isGift && " · подарок (−100%)"}
                      {!it.isGift && (!!it.discountPct || !!it.discountRub) && (
                        <span className="text-amber-200">
                          {" · скидка −"}{money(lineDiscount(it))}
                          {it.discountPct ? ` (${it.discountPct}%)` : ""}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 bg-ink-800 rounded-lg p-1">
                  <button onClick={() => update(it.productId, { qty: Math.max(1, it.qty - 1) })} className="px-1.5 text-mute hover:text-white">
                    <Minus size={14} />
                  </button>
                  <span className="w-6 text-center text-white text-sm">{it.qty}</span>
                  <button onClick={() => update(it.productId, { qty: it.qty + 1 })} className="px-1.5 text-mute hover:text-white">
                    <Plus size={14} />
                  </button>
                </div>
                <div className="w-24 text-right font-semibold text-white">
                  {it.noPrice ? "—" : money(lineTotal(it))}
                </div>
                <button onClick={() => onChange(items.filter((x) => x.productId !== it.productId))} className="text-mute hover:text-white">
                  <Trash2 size={15} />
                </button>
              </div>
              {!it.noPrice && (
                <div className="flex gap-2 mt-2 pl-0">
                  <input
                    className="input py-1.5 text-[13px] w-24"
                    placeholder="скидка %"
                    value={it.discountPct ?? ""}
                    onChange={(e) => update(it.productId, { discountPct: Number(e.target.value) || undefined, isGift: false })}
                  />
                  <input
                    className="input py-1.5 text-[13px] w-28"
                    placeholder="скидка ₽"
                    value={it.discountRub ?? ""}
                    onChange={(e) => update(it.productId, { discountRub: Number(e.target.value) || undefined, isGift: false })}
                  />
                  <button
                    onClick={() => update(it.productId, { isGift: !it.isGift })}
                    className={`chip ${it.isGift ? "bg-gold/20 text-gold-soft" : "bg-ink-700 text-mute"}`}
                  >
                    🎁 Подарок
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
