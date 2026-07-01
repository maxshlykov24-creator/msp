import { useEffect, useRef, useState } from "react";
import { Search, Plus, Minus, Trash2, ScanLine, X, Loader2 } from "lucide-react";
import type { CartItem, Product } from "../data/types";
import { PRODUCTS } from "../data/mock";
import { money } from "../lib/format";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { BarcodeScannerModal } from "./BarcodeScanner";

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
  const { activeStore } = useStore();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Поиск: моки — синхронный локальный фильтр; прод — debounce к /catalog/search.
  useEffect(() => {
    if (!q.trim()) {
      setFound([]);
      setLoading(false);
      return;
    }
    if (USE_MOCK) {
      const qNorm = q.trim().toLowerCase();
      setFound(
        PRODUCTS.filter(
          (p) => p.name.toLowerCase().includes(qNorm) || p.sku.toLowerCase().includes(qNorm)
        )
      );
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.get<Product[]>(
          `/catalog/search?q=${encodeURIComponent(q.trim())}&store=${encodeURIComponent(activeStore)}&limit=30`
        );
        setFound(res);
      } catch {
        setFound([]);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q, activeStore]);

  function addProduct(p: Product) {
    const existing = items.find((i) => i.productId === p.id);
    if (existing) {
      onChange(items.map((i) => (i.productId === p.id ? { ...i, qty: i.qty + 1 } : i)));
    } else {
      onChange([
        ...items,
        { productId: p.id, name: p.name, price: noPrice ? 0 : p.price, qty: 1, noPrice },
      ]);
    }
    setQ("");
    setFound([]);
    setOpen(false);
  }

  function update(id: string, patch: Partial<CartItem>) {
    onChange(items.map((i) => (i.productId === id ? { ...i, ...patch } : i)));
  }

  async function handleScanned(code: string) {
    setScannerOpen(false);
    try {
      const list = USE_MOCK
        ? PRODUCTS.filter((p) => p.sku === code)
        : await api.get<Product[]>(
            `/catalog/search?q=${encodeURIComponent(code)}&store=${encodeURIComponent(activeStore)}&limit=5`
          );
      if (list.length === 1) {
        addProduct(list[0]);
        setNotice(`Добавлено: ${list[0].name}`);
      } else if (list.length > 1) {
        setQ(code);
        setOpen(true);
        setNotice(null);
      } else {
        setNotice(`Товар со штрихкодом «${code}» не найден в каталоге`);
      }
    } catch {
      setNotice("Не удалось выполнить поиск по штрихкоду");
    }
    setTimeout(() => setNotice(null), 4000);
  }

  return (
    <div className="space-y-3">
      {scannerOpen && (
        <BarcodeScannerModal onDetected={handleScanned} onClose={() => setScannerOpen(false)} />
      )}
      {notice && (
        <div className="rounded-lg border border-gold/30 bg-gold/10 text-gold-soft text-[13px] px-3 py-2">
          {notice}
        </div>
      )}
      <div className="relative">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
            <input
              className="input pl-9"
              placeholder="Поиск товара, артикул или штрихкод…"
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
            title="Сканировать штрихкод камерой"
            type="button"
            onClick={() => setScannerOpen(true)}
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
            {loading && (
              <div className="px-3 py-3 text-mute text-sm flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Поиск…
              </div>
            )}
            {!loading && found.length === 0 && (
              <div className="px-3 py-3 text-mute text-sm">Ничего не найдено</div>
            )}
            {!loading &&
              found.map((p) => (
                <button
                  key={p.id}
                  onClick={() => addProduct(p)}
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-ink-800 flex items-center gap-3"
                >
                  <div className="flex-1">
                    <div className="text-[14px] text-white">{p.name}</div>
                    <div className="text-[12px] text-mute">
                      {p.sku} · {p.category} · ост. {p.stock}
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
