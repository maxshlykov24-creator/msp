import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRightLeft,
  Search,
  Plus,
  Minus,
  Trash2,
  ScanLine,
  X,
  Loader2,
  ChevronDown,
  ChevronRight,
  Unlink,
  Link2,
} from "lucide-react";
import type { CartItem, Product } from "../data/types";
import { PRODUCTS } from "../data/mock";
import { STORE_TO_WAREHOUSE, SUIT_PART_LABEL } from "@kassa/shared";
import type { SuitPart } from "@kassa/shared";
import { money } from "../lib/format";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { BarcodeScannerModal } from "./BarcodeScanner";
import { Modal } from "./ui";
import { MovementModal } from "./MovementModal";

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

/** Полная сумма позиций без скидок (до %). */
export function cartGross(items: CartItem[]): number {
  return items.reduce((s, it) => {
    if (it.noPrice) return s;
    return s + it.price * it.qty;
  }, 0);
}

/** Сумма всех скидок по позициям (₽). */
export function cartItemsDiscount(items: CartItem[]): number {
  return items.reduce((s, it) => s + lineDiscount(it), 0);
}

interface WarehouseStockLine {
  warehouseMsId: string;
  name: string;
  available: number;
  reserve: number;
  stock: number;
}

interface ProductStockResponse {
  productId: string;
  name: string;
  warehouses: WarehouseStockLine[];
}

function fmtQty(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\.?0+$/, "");
}

interface SuitCartWarning {
  productId: string;
  variation: string;
  title: string;
  soldPart: SuitPart;
  size: string | null;
  missing: SuitPart[];
  pairs: Array<{ part: SuitPart; qty: number }>;
}

/**
 * Продажа части костюма без пары оставляет полупарк. Предупреждаем до чека:
 * показываем, какая парная часть есть на складе, чтобы консультант предложил
 * клиенту костюм целиком, а не разбивал модель.
 */
function SuitBreakWarning({ items, store }: { items: CartItem[]; store: string }) {
  const [warnings, setWarnings] = useState<SuitCartWarning[]>([]);

  const payload = useMemo(
    () =>
      items
        .filter((it) => !it.isReturn && it.qty > 0 && it.productId)
        .map((it) => ({ productId: it.productId, qty: it.qty })),
    [items]
  );

  useEffect(() => {
    if (USE_MOCK || payload.length === 0) {
      setWarnings([]);
      return;
    }
    let alive = true;
    const t = setTimeout(() => {
      api
        .post<{ warnings: SuitCartWarning[]; suits: number }>("/suits/cart-check", {
          store,
          items: payload,
        })
        .then((res) => {
          if (alive) setWarnings(res.warnings ?? []);
        })
        .catch(() => {
          if (alive) setWarnings([]);
        });
    }, 400);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [payload, store]);

  if (warnings.length === 0) return null;

  return (
    <div className="mt-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2.5 space-y-1.5">
      <div className="text-[13px] font-semibold text-amber-200">
        Костюм разбивается: останется полупарк
      </div>
      {warnings.map((w) => (
        <div key={w.productId} className="text-[12px] text-amber-100/90">
          {SUIT_PART_LABEL[w.soldPart]}
          {w.size ? ` ${w.size}` : ""} · {w.title} ({w.variation}) уходит без{" "}
          {w.missing.map((part) => SUIT_PART_LABEL[part]).join(" и ")}. На складе есть{" "}
          {w.pairs.map((pair) => `${SUIT_PART_LABEL[pair.part]} ${pair.qty} шт.`).join(", ")} —
          предложи костюм целиком.
        </div>
      ))}
    </div>
  );
}

interface SuitPriceGroupPart {
  productId: string;
  part: SuitPart;
  size: string;
  qty: number;
  unitMsPriceRub: number;
  distributedUnitPriceRub: number;
}

interface SuitPriceGroupApi {
  groupId: string;
  variation: string;
  title: string;
  hasVest: boolean;
  qty: number;
  matrixUnitPriceRub: number | null;
  matchedRuleLabel: string | null;
  parts: SuitPriceGroupPart[];
}

/**
 * Склейка частей костюма в одну строку чека по цене матрицы (созвон 09.09):
 * при скане пиджака + брюк (+ жилета) одной вариации сервер `/suits/price-group`
 * подбирает цену из матрицы и разносит её по частям. Разделённые вручную
 * позиции (`suitSplit`) автосклейка не трогает, пока их не собрали обратно.
 * Возвращает снимок групп по `groupId` — используется и для применения цены,
 * и для рендера (название, часть каждой позиции, признак «не сматчилось»).
 */
function useSuitGrouping(items: CartItem[], onChange: (items: CartItem[]) => void) {
  const [meta, setMeta] = useState<Record<string, SuitPriceGroupApi>>({});

  const payload = useMemo(
    () =>
      items
        .filter((it) => !it.isReturn && it.qty > 0 && it.productId && !it.suitSplit)
        .map((it) => ({ productId: it.productId, qty: it.qty })),
    [items]
  );

  useEffect(() => {
    if (USE_MOCK || payload.length === 0) return;
    let alive = true;
    const t = setTimeout(() => {
      api
        .post<{ groups: SuitPriceGroupApi[]; unmatchedProductIds: string[] }>("/suits/price-group", {
          items: payload,
        })
        .then((res) => {
          if (!alive) return;
          const groups = res.groups ?? [];
          const nextMeta: Record<string, SuitPriceGroupApi> = {};
          for (const g of groups) nextMeta[g.groupId] = g;
          setMeta(nextMeta);

          let changed = false;
          const next = items.map((it) => {
            if (it.suitSplit) return it;
            for (const g of groups) {
              const part = g.parts.find((p) => p.productId === it.productId);
              if (!part) continue;
              const matched = g.matrixUnitPriceRub != null;
              const nextPrice = matched ? part.distributedUnitPriceRub : it.suitOriginalPrice ?? it.price;
              if (it.suitGroupId === g.groupId && it.suitPriceApplied === matched && it.price === nextPrice) {
                return it;
              }
              changed = true;
              return {
                ...it,
                suitGroupId: g.groupId,
                suitPriceApplied: matched,
                suitOriginalPrice: it.suitOriginalPrice ?? it.price,
                price: nextPrice,
              };
            }
            return it;
          });
          if (changed) onChange(next);
        })
        .catch(() => {
          /* сеть недоступна — оставляем цены МойСклад, костюм не склеиваем */
        });
    }, 400);
    return () => {
      alive = false;
      clearTimeout(t);
    };
    // payload сравнивается по значению через JSON в зависимости эффекта ниже
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(payload)]);

  return meta;
}

export function ProductPicker({
  items,
  onChange,
  noPrice = false,
  /** Если задано — можно добавлять только позиции из этой сделки (возврат/обмен). */
  allowedFrom,
  readOnly = false,
  dealNumber,
  showMovement = true,
  onMovementCreated,
}: {
  items: CartItem[];
  onChange: (items: CartItem[]) => void;
  noPrice?: boolean;
  allowedFrom?: CartItem[];
  readOnly?: boolean;
  dealNumber?: number;
  showMovement?: boolean;
  onMovementCreated?: (summary: string) => void;
}) {
  const { activeStore } = useStore();
  const suitGroupMeta = useSuitGrouping(items, onChange);
  const [expandedSuits, setExpandedSuits] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const allowedMap = useMemo(() => {
    if (!allowedFrom?.length) return null;
    const map = new Map<string, { maxQty: number; template: CartItem }>();
    for (const it of allowedFrom) {
      if (it.isReturn || it.price < 0) continue;
      const prev = map.get(it.productId);
      if (prev) {
        prev.maxQty += it.qty;
      } else {
        map.set(it.productId, {
          maxQty: it.qty,
          template: {
            ...it,
            price: Math.abs(it.price),
            isReturn: undefined,
            qty: 1,
          },
        });
      }
    }
    return map;
  }, [allowedFrom]);

  const allowedList = useMemo(() => {
    if (!allowedMap) return [] as CartItem[];
    return [...allowedMap.values()].map((v) => v.template);
  }, [allowedMap]);

  // productId → доступно на текущем шоуруме (из поиска при добавлении; уточняется в модалке)
  const [availableById, setAvailableById] = useState<Record<string, number>>({});
  const [stockModal, setStockModal] = useState<{
    productId: string;
    name: string;
    loading: boolean;
    data: ProductStockResponse | null;
    error: string | null;
  } | null>(null);

  const warehouseName =
    (STORE_TO_WAREHOUSE as Record<string, string | null>)[activeStore] ?? activeStore;

  function flash(msg: string) {
    setNotice(msg);
    setTimeout(() => setNotice(null), 4000);
  }

  function maxQtyFor(productId: string): number | null {
    if (!allowedMap) return null;
    return allowedMap.get(productId)?.maxQty ?? 0;
  }

  function remainingQty(productId: string): number | null {
    const max = maxQtyFor(productId);
    if (max == null) return null;
    const used = items.find((i) => i.productId === productId)?.qty ?? 0;
    return Math.max(0, max - used);
  }

  // Поиск: при allowedFrom — только позиции исходной сделки; иначе каталог.
  useEffect(() => {
    if (allowedMap) {
      setLoading(false);
      const tokens = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const list = allowedList
        .filter((it) => {
          if (!tokens.length) return true;
          const hay = `${it.name} ${it.productId}`.toLowerCase();
          return tokens.every((t) => hay.includes(t));
        })
        .map(
          (it): Product => ({
            id: it.productId,
            name: it.name,
            sku: it.productId,
            category: "Из сделки",
            price: it.price,
            store: activeStore,
            stock: remainingQty(it.productId) ?? it.qty,
          })
        );
      setFound(list);
      return;
    }
    if (!q.trim()) {
      setFound([]);
      setLoading(false);
      return;
    }
    if (USE_MOCK) {
      const tokens = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
      setFound(
        PRODUCTS.filter((p) => {
          const hay = `${p.name} ${p.sku}`.toLowerCase();
          return tokens.every((t) => hay.includes(t));
        })
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
  }, [q, activeStore, allowedMap, allowedList, items]);

  function addProduct(p: Product) {
    if (allowedMap) {
      const entry = allowedMap.get(p.id);
      if (!entry) {
        flash("Этой позиции не было в исходной продаже — добавить нельзя");
        return;
      }
      const used = items.find((i) => i.productId === p.id)?.qty ?? 0;
      if (used >= entry.maxQty) {
        flash(`В исходной сделке было только ${entry.maxQty} шт.`);
        return;
      }
      setAvailableById((prev) => ({ ...prev, [p.id]: entry.maxQty }));
      if (used > 0) {
        onChange(items.map((i) => (i.productId === p.id ? { ...i, qty: i.qty + 1 } : i)));
      } else {
        onChange([
          ...items,
          {
            ...entry.template,
            qty: 1,
            price: noPrice ? 0 : entry.template.price,
            noPrice: noPrice || entry.template.noPrice,
          },
        ]);
      }
      setQ("");
      setFound([]);
      setOpen(false);
      return;
    }

    setAvailableById((prev) => ({ ...prev, [p.id]: p.stock }));
    const existing = items.find((i) => i.productId === p.id);
    if (existing) {
      onChange(items.map((i) => (i.productId === p.id ? { ...i, qty: i.qty + 1 } : i)));
    } else {
      onChange([
        ...items,
        {
          productId: p.id,
          name: p.name,
          price: noPrice ? 0 : p.price,
          qty: 1,
          category: p.category,
          noPrice,
          barcode: p.barcode,
          itemStatus: "waiting",
        },
      ]);
    }
    setQ("");
    setFound([]);
    setOpen(false);
  }

  function update(id: string, patch: Partial<CartItem>) {
    if (patch.qty != null && allowedMap) {
      const max = allowedMap.get(id)?.maxQty;
      if (max != null && patch.qty > max) {
        flash(`В исходной сделке было только ${max} шт.`);
        patch = { ...patch, qty: max };
      }
    }
    onChange(items.map((i) => (i.productId === id ? { ...i, ...patch } : i)));
  }

  /** Обновить скидку/подарок сразу по всем частям костюма в группе. */
  function updateGroup(groupId: string, patch: Partial<CartItem>) {
    onChange(items.map((i) => (i.suitGroupId === groupId ? { ...i, ...patch } : i)));
  }

  /** «Разделить на части»: цены частей возвращаются к прайсу МойСклад, автосклейка не трогает. */
  function splitSuit(groupId: string) {
    onChange(
      items.map((i) =>
        i.suitGroupId === groupId
          ? { ...i, suitSplit: true, suitPriceApplied: false, price: i.suitOriginalPrice ?? i.price }
          : i
      )
    );
  }

  /** «Собрать костюм»: возвращает части в автосклейку, цена матрицы подтянется следующим тиком. */
  function mergeSuit(groupId: string) {
    onChange(items.map((i) => (i.suitGroupId === groupId ? { ...i, suitSplit: false } : i)));
  }

  function toggleSuitExpanded(groupId: string) {
    setExpandedSuits((prev) => ({ ...prev, [groupId]: !prev[groupId] }));
  }

  // Строки чека: собранные (не разделённые) части одного костюма сворачиваются
  // в одну карточку, всё остальное — обычные строки, как раньше.
  const displayRows = useMemo(() => {
    const rows: Array<{ kind: "single"; item: CartItem } | { kind: "suit"; groupId: string; items: CartItem[] }> = [];
    const seenGroups = new Set<string>();
    for (const it of items) {
      if (it.suitGroupId && !it.suitSplit) {
        if (seenGroups.has(it.suitGroupId)) continue;
        seenGroups.add(it.suitGroupId);
        const groupItems = items.filter((x) => x.suitGroupId === it.suitGroupId && !x.suitSplit);
        rows.push({ kind: "suit", groupId: it.suitGroupId, items: groupItems });
      } else {
        rows.push({ kind: "single", item: it });
      }
    }
    return rows;
  }, [items]);

  async function openStockModal(it: CartItem) {
    setStockModal({ productId: it.productId, name: it.name, loading: true, data: null, error: null });
    if (USE_MOCK) {
      const p = PRODUCTS.find((x) => x.id === it.productId);
      setStockModal({
        productId: it.productId,
        name: it.name,
        loading: false,
        error: null,
        data: {
          productId: it.productId,
          name: it.name,
          warehouses: [
            {
              warehouseMsId: "pyat",
              name: "На Новокузнецкой",
              available: Math.max(0, (p?.stock ?? 0) - 1),
              reserve: 1,
              stock: p?.stock ?? 0,
            },
            {
              warehouseMsId: "bauman",
              name: "На Бауманской",
              available: p?.stock ?? 0,
              reserve: 0,
              stock: p?.stock ?? 0,
            },
          ],
        },
      });
      return;
    }
    try {
      const data = await api.get<ProductStockResponse>(`/catalog/${encodeURIComponent(it.productId)}/stock`);
      const list = Array.isArray(data?.warehouses) ? data.warehouses : [];
      const wh =
        list.find((w) => w.name === warehouseName) ??
        list.find((w) => w.name.includes(activeStore.replace(/^На /, "")));
      if (wh) setAvailableById((prev) => ({ ...prev, [it.productId]: wh.available }));
      setStockModal({
        productId: it.productId,
        name: it.name,
        loading: false,
        data: { ...data, warehouses: list, productId: data?.productId ?? it.productId, name: data?.name ?? it.name },
        error: list.length === 0 ? "Нет данных по складам" : null,
      });
    } catch (err) {
      const msg =
        err instanceof Error && err.message
          ? err.message
          : "Не удалось загрузить остатки";
      setStockModal({
        productId: it.productId,
        name: it.name,
        loading: false,
        data: null,
        error: msg,
      });
    }
  }

  async function handleScanned(code: string) {
    setScannerOpen(false);
    try {
      if (allowedMap) {
        const qNorm = code.trim().toLowerCase();
        const match = allowedList.find(
          (it) =>
            it.productId.toLowerCase() === qNorm ||
            it.name.toLowerCase().includes(qNorm)
        );
        if (!match) {
          flash("Штрихкод не из исходной продажи — добавить нельзя");
          return;
        }
        addProduct({
          id: match.productId,
          name: match.name,
          sku: match.productId,
          category: "Из сделки",
          price: match.price,
          store: activeStore,
          stock: remainingQty(match.productId) ?? match.qty,
        });
        return;
      }
      const list = USE_MOCK
        ? PRODUCTS.filter((p) => p.sku === code)
        : await api.get<Product[]>(
            `/catalog/search?q=${encodeURIComponent(code)}&store=${encodeURIComponent(activeStore)}&limit=5`
          );
      if (list.length === 1) {
        addProduct(list[0]);
        flash(`Добавлено: ${list[0].name}`);
      } else if (list.length > 1) {
        setQ(code);
        setOpen(true);
        setNotice(null);
      } else {
        flash(`Товар со штрихкодом «${code}» не найден в каталоге`);
      }
    } catch {
      flash("Не удалось выполнить поиск по штрихкоду");
    }
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
      {!readOnly && <div className="relative">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
            <input
              className="input pl-9"
              placeholder={
                allowedMap
                    ? "Поиск только среди позиций исходной сделки…"
                    : "Вариация, артикул, цвет или штрихкод…"
              }
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
        {open && (allowedMap || q) && (
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
              <div className="px-3 py-3 text-mute text-sm">
                {allowedMap ? "В исходной сделке нет подходящих позиций" : "Ничего не найдено"}
              </div>
            )}
            {!loading &&
              found.map((p) => (
                <button
                  key={p.id}
                  onClick={() => addProduct(p)}
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-ink-800 flex items-center gap-3"
                >
                  <div className="flex-1">
                    <div className="text-[14px] text-white break-words whitespace-normal leading-snug">{p.name}</div>
                    <div className="text-[12px] text-mute">
                      {allowedMap
                        ? `из сделки · ещё можно ${fmtQty(remainingQty(p.id) ?? 0)} шт`
                        : `${p.sku} · ${p.category} · доступно ${fmtQty(p.stock)}`}
                    </div>
                  </div>
                  {!noPrice && <div className="text-gold-soft font-semibold">{money(p.price)}</div>}
                  <Plus size={16} className="text-gold" />
                </button>
              ))}
          </div>
        )}
      </div>}

      {items.length > 0 && (
        <>
          <div className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
            {displayRows.map((row) => {
              if (row.kind === "suit") {
                const { groupId, items: groupItems } = row;
                const meta = suitGroupMeta[groupId];
                const expanded = !!expandedSuits[groupId];
                const groupTotal = groupItems.reduce((s, it) => s + lineTotal(it), 0);
                const groupQty = groupItems[0]?.qty ?? 1;
                const partOf = (productId: string): SuitPart | undefined =>
                  meta?.parts.find((p) => p.productId === productId)?.part;
                const first = groupItems[0];
                const groupDiscountPct = first?.discountPct;
                const groupDiscountRub = first?.discountRub;
                const groupIsGift = !!first?.isGift;
                return (
                  <div key={groupId} className="px-3 py-2.5 bg-ink-900/50">
                    <div className="flex items-start gap-3">
                      <button
                        type="button"
                        onClick={() => toggleSuitExpanded(groupId)}
                        className="flex-1 text-left min-w-0 rounded-lg hover:bg-ink-800/60 -mx-1 px-1 py-0.5 transition flex items-start gap-1.5"
                      >
                        {expanded ? (
                          <ChevronDown size={15} className="mt-0.5 text-mute shrink-0" />
                        ) : (
                          <ChevronRight size={15} className="mt-0.5 text-mute shrink-0" />
                        )}
                        <div className="min-w-0">
                          <div className="text-[14px] text-white break-words whitespace-normal leading-snug">
                            {meta?.title ?? "Костюм"} · {groupItems.map((it) => it.name).join(" + ")}
                          </div>
                          <div className="text-[12px] text-mute flex flex-wrap gap-x-2 mt-0.5">
                            <span>{money((first?.price ?? 0) as number)} / шт</span>
                            {meta && meta.matrixUnitPriceRub == null && (
                              <span className="text-amber-300/90">
                                цена костюма не определена — цены частей из МойСклад
                              </span>
                            )}
                            {groupIsGift && <span>подарок (−100%)</span>}
                            {!groupIsGift && (!!groupDiscountPct || !!groupDiscountRub) && (
                              <span className="text-amber-200">
                                скидка −{money(groupItems.reduce((s, it) => s + lineDiscount(it), 0))}
                                {groupDiscountPct ? ` (${groupDiscountPct}%)` : ""}
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => splitSuit(groupId)}
                        title="Разделить на части — дальше продаются по отдельным ценам МойСклад"
                        className="inline-flex items-center gap-1 rounded-lg border border-ink-700 px-2 py-1 text-[12px] text-mute hover:text-white hover:border-gold/40 shrink-0 mt-0.5"
                      >
                        <Unlink size={13} /> Разделить
                      </button>
                      <span className="w-8 text-center text-white text-sm shrink-0 mt-1">×{groupQty}</span>
                      <div className="w-24 text-right font-semibold text-white shrink-0 mt-0.5">
                        {money(groupTotal)}
                      </div>
                    </div>
                    {expanded && (
                      <div className="mt-2 ml-5 space-y-1 border-l border-ink-700 pl-3">
                        {groupItems.map((it) => {
                          const part = partOf(it.productId);
                          return (
                            <div key={it.productId} className="text-[12px] text-mute flex justify-between gap-2">
                              <span className="break-words">
                                {part ? SUIT_PART_LABEL[part] : ""} · {it.name}
                              </span>
                              <span className="shrink-0 text-white">{money(it.price)}</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {!readOnly && (
                      <div className="flex flex-wrap gap-2 mt-2 items-center">
                        <input
                          className="input py-1.5 text-[13px] w-24"
                          placeholder="скидка %"
                          value={groupDiscountPct ?? ""}
                          onChange={(e) =>
                            updateGroup(groupId, {
                              discountPct: Number(e.target.value) || undefined,
                              isGift: false,
                            })
                          }
                        />
                        <input
                          className="input py-1.5 text-[13px] w-28"
                          placeholder="скидка ₽"
                          value={groupDiscountRub ?? ""}
                          onChange={(e) =>
                            updateGroup(groupId, {
                              discountRub: Number(e.target.value) || undefined,
                              isGift: false,
                            })
                          }
                        />
                        <button
                          onClick={() => updateGroup(groupId, { isGift: !groupIsGift })}
                          className={`chip ${groupIsGift ? "bg-gold/20 text-gold-soft" : "bg-ink-700 text-mute"}`}
                        >
                          🎁 Подарок
                        </button>
                      </div>
                    )}
                  </div>
                );
              }

              const it = row.item;
              const avail = availableById[it.productId];
              return (
                <div key={it.productId} className="px-3 py-2.5 bg-ink-900/50">
                  <div className="flex items-start gap-3">
                    <button
                      type="button"
                      onClick={() => openStockModal(it)}
                      className="flex-1 text-left min-w-0 rounded-lg hover:bg-ink-800/60 -mx-1 px-1 py-0.5 transition"
                      title="Остатки по складам"
                    >
                      <div className="text-[14px] text-white break-words whitespace-normal leading-snug">{it.name}</div>
                      <div className="text-[12px] text-mute flex flex-wrap gap-x-2 mt-0.5">
                        {!it.noPrice && <span>{money(it.price)} / шт</span>}
                        {avail !== undefined && (
                          <span
                            className={
                              avail <= 0
                                ? "text-amber-300/90"
                                : avail < it.qty
                                  ? "text-amber-200"
                                  : "text-emerald-300/90"
                            }
                          >
                            доступно {fmtQty(avail)}
                          </span>
                        )}
                        {it.isGift && <span>подарок (−100%)</span>}
                        {!it.isGift && (!!it.discountPct || !!it.discountRub) && (
                          <span className="text-amber-200">
                            скидка −{money(lineDiscount(it))}
                            {it.discountPct ? ` (${it.discountPct}%)` : ""}
                          </span>
                        )}
                        {it.suitGroupId && it.suitSplit && (
                          <span className="text-mute-soft">часть разделённого костюма</span>
                        )}
                      </div>
                    </button>
                    {!readOnly && it.suitGroupId && it.suitSplit && (
                      <button
                        type="button"
                        onClick={() => mergeSuit(it.suitGroupId!)}
                        title="Собрать костюм обратно — цена матрицы подтянется автоматически"
                        className="inline-flex items-center gap-1 rounded-lg border border-ink-700 px-2 py-1 text-[12px] text-mute hover:text-white hover:border-gold/40 shrink-0 mt-0.5"
                      >
                        <Link2 size={13} /> Собрать костюм
                      </button>
                    )}
                    {!readOnly && (
                      <div className="flex items-center gap-1 bg-ink-800 rounded-lg p-1 shrink-0 mt-0.5">
                        <button
                          onClick={() => update(it.productId, { qty: Math.max(1, it.qty - 1) })}
                          className="px-1.5 text-mute hover:text-white"
                        >
                          <Minus size={14} />
                        </button>
                        <span className="w-6 text-center text-white text-sm">{it.qty}</span>
                        <button
                          onClick={() => {
                            const max = maxQtyFor(it.productId);
                            const next = it.qty + 1;
                            if (max != null && next > max) {
                              flash(`В исходной сделке было только ${max} шт.`);
                              return;
                            }
                            update(it.productId, { qty: next });
                          }}
                          className="px-1.5 text-mute hover:text-white"
                        >
                          <Plus size={14} />
                        </button>
                      </div>
                    )}
                    {readOnly && (
                      <span className="w-8 text-center text-white text-sm shrink-0 mt-1">×{it.qty}</span>
                    )}
                    <div className="w-24 text-right font-semibold text-white shrink-0 mt-0.5">
                      {it.noPrice ? "—" : money(lineTotal(it))}
                    </div>
                    {!readOnly && (
                      <button
                        onClick={() => onChange(items.filter((x) => x.productId !== it.productId))}
                        className="text-mute hover:text-white shrink-0 mt-0.5"
                      >
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                  {!readOnly && !it.noPrice && (
                    <div className="flex flex-wrap gap-2 mt-2 items-center">
                      <input
                        className="input py-1.5 text-[13px] w-24"
                        placeholder="скидка %"
                        value={it.discountPct ?? ""}
                        onChange={(e) =>
                          update(it.productId, {
                            discountPct: Number(e.target.value) || undefined,
                            isGift: false,
                          })
                        }
                      />
                      <input
                        className="input py-1.5 text-[13px] w-28"
                        placeholder="скидка ₽"
                        value={it.discountRub ?? ""}
                        onChange={(e) =>
                          update(it.productId, {
                            discountRub: Number(e.target.value) || undefined,
                            isGift: false,
                          })
                        }
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
              );
            })}
            {(() => {
              const itemsDisc = cartItemsDiscount(items);
              if (itemsDisc <= 0) return null;
              const gross = cartGross(items);
              const after = Math.max(0, gross - itemsDisc);
              return (
                <div className="px-3 py-2.5 bg-amber-400/5 border-t border-ink-700 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <div className="text-[13px] text-amber-200 font-medium">
                    Скидка по позициям: −{money(itemsDisc)}
                  </div>
                  <div className="text-[12px] text-mute whitespace-nowrap">
                    {money(gross)} → {money(after)}
                  </div>
                </div>
              );
            })()}
          </div>
          <SuitBreakWarning items={items} store={activeStore} />
          {showMovement && (
            <div className="flex justify-end mt-2">
              <button
                type="button"
                onClick={() => setMoveOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-gold-soft hover:border-gold/40 hover:text-white"
              >
                <ArrowRightLeft size={15} /> Перемещение
              </button>
            </div>
          )}
        </>
      )}

      <MovementModal
        open={moveOpen}
        onClose={() => setMoveOpen(false)}
        dealNumber={dealNumber}
        store={activeStore}
        items={items}
        onCreated={onMovementCreated}
      />

      <Modal
        open={!!stockModal}
        onClose={() => setStockModal(null)}
        title={stockModal ? `Остатки · ${stockModal.name}` : "Остатки"}
        wide
      >
        {stockModal?.loading && (
          <div className="flex items-center gap-2 text-mute text-sm py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Загрузка из МойСклад…
          </div>
        )}
        {stockModal?.error && (
          <div className="text-amber-300/90 text-sm py-4 text-center">{stockModal.error}</div>
        )}
        {stockModal?.data && (
          <div className="overflow-x-hidden">
            <table className="w-full text-sm table-fixed">
              <thead>
                <tr className="text-left text-mute border-b border-ink-700">
                  <th className="pb-2 pr-2 font-medium">Склад</th>
                  <th className="pb-2 px-1 font-medium text-right w-[22%]">Доступно</th>
                  <th className="pb-2 px-1 font-medium text-right w-[18%]">Резерв</th>
                  <th className="pb-2 pl-1 font-medium text-right w-[18%]">Остаток</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {stockModal.data.warehouses.map((w) => {
                  const isCurrent = w.name === warehouseName;
                  return (
                    <tr
                      key={w.warehouseMsId}
                      className={isCurrent ? "bg-gold/10" : undefined}
                    >
                      <td className="py-2.5 pr-2 text-white break-words whitespace-normal">
                        {w.name}
                        {isCurrent && (
                          <span className="ml-1.5 text-[11px] text-gold-soft whitespace-nowrap">текущий</span>
                        )}
                      </td>
                      <td className="py-2.5 px-1 text-right font-semibold text-emerald-300/90">
                        {fmtQty(w.available)}
                      </td>
                      <td className="py-2.5 px-1 text-right text-mute-soft">{fmtQty(w.reserve)}</td>
                      <td className="py-2.5 pl-1 text-right text-white">{fmtQty(w.stock)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="hint-only mt-3 text-[12px] text-mute">
              Доступно = остаток − резерв. Данные с МойСклад на момент открытия.
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
