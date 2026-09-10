import { useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, CheckCircle2, Loader2, Plus, Trash2 } from "lucide-react";
import {
  CENTRAL_WAREHOUSE,
  ITEM_LOCATIONS,
  MOVEMENT_TARGETS,
  findWarehouseId,
  isCentralWarehouse,
  shortIdempotencyKey,
  taskTitle,
  type Product,
} from "@kassa/shared";
import type { CartItem } from "../data/types";
import { api, USE_MOCK } from "../api/client";
import { Button, Modal, Select, opts } from "./ui";

interface WarehouseRef {
  id: string;
  name: string;
}

interface WarehouseStockLine {
  warehouseMsId: string;
  name: string;
  available: number;
  reserve: number;
  stock: number;
}

type LinePlan = {
  productId: string;
  selected: boolean;
  from: string;
  qty: number;
};

function fmtQty(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\.?0+$/, "");
}

function productToCartItem(p: Product): CartItem {
  return {
    productId: p.id,
    name: p.name,
    price: p.price ?? 0,
    qty: 1,
    barcode: p.barcode,
  };
}

const EMPTY_ITEMS: CartItem[] = [];

/**
 * Окно перемещения: позиции из заявки или поиск в каталоге (без заявки),
 * у каждой свой «откуда», клик по названию — остатки по складам.
 */
/** Куда везти по умолчанию — склад шоурума заявки. */
export function defaultMovementTarget(store: string): string {
  if (/бауман/i.test(store)) return "На Бауманской";
  if (/пятниц|новокузнецк/i.test(store)) return "На Новокузнецкой";
  return MOVEMENT_TARGETS[0] ?? "На Бауманской";
}

export function MovementModal({
  open,
  onClose,
  dealNumber,
  store,
  items: initialItems,
  allowCatalogPick = false,
  defaultTarget,
  /** Перед созданием задачи: сохранить заявку и вернуть серверный номер (форма создания). */
  ensureDeal,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  dealNumber?: number;
  store: string;
  items?: CartItem[];
  /** Без заявки: поиск и добавление товаров из каталога. */
  allowCatalogPick?: boolean;
  /** Склад-получатель по умолчанию (иначе — по store / первый из списка). */
  defaultTarget?: string;
  ensureDeal?: () => Promise<{ dealNumber: number; store: string } | null>;
  onCreated?: (summary: string) => void;
}) {
  const seedItems = initialItems ?? EMPTY_ITEMS;
  const [items, setItems] = useState<CartItem[]>(seedItems);
  const [warehouses, setWarehouses] = useState<WarehouseRef[]>([]);
  const [target, setTarget] = useState(MOVEMENT_TARGETS[0]!);
  const [lines, setLines] = useState<LinePlan[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [stockFor, setStockFor] = useState<{
    productId: string;
    name: string;
    loading: boolean;
    error?: string;
    warehouses?: WarehouseStockLine[];
  } | null>(null);

  const sourceOptions = useMemo(
    () => ITEM_LOCATIONS.filter((name) => !name.startsWith("СДЭК")),
    []
  );

  function finishSuccess() {
    if (success) onCreated?.(success);
    setSuccess(null);
    onClose();
  }

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSuccess(null);
    const preset = defaultTarget || defaultMovementTarget(store);
    setTarget((MOVEMENT_TARGETS as string[]).includes(preset) ? preset : MOVEMENT_TARGETS[0]!);
    setQ("");
    setHits([]);
    setItems(seedItems);
    setLines(
      seedItems.map((item) => ({
        productId: item.productId,
        selected: true,
        from: CENTRAL_WAREHOUSE,
        qty: item.qty,
      }))
    );
    if (USE_MOCK) return;
    api
      .get<{ warehouses: WarehouseRef[] }>("/catalog/references")
      .then((refs) => setWarehouses(refs.warehouses ?? []))
      .catch(() => {});
  }, [open, seedItems, store, defaultTarget]);

  useEffect(() => {
    if (!open || !allowCatalogPick) return;
    const query = q.trim();
    if (query.length < 2) {
      setHits([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          if (USE_MOCK) {
            if (!cancelled) setHits([]);
            return;
          }
          const rows = await api.getQuery<Product[]>("/catalog/search", {
            q: query,
            store,
            limit: 20,
          });
          if (!cancelled) setHits(rows ?? []);
        } catch {
          if (!cancelled) setHits([]);
        } finally {
          if (!cancelled) setSearching(false);
        }
      })();
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [q, open, allowCatalogPick, store]);

  function warehouseId(name: string): string | undefined {
    return findWarehouseId(warehouses, name);
  }

  function patchLine(productId: string, patch: Partial<LinePlan>) {
    setLines((prev) => prev.map((row) => (row.productId === productId ? { ...row, ...patch } : row)));
  }

  function addProduct(p: Product) {
    setItems((prev) => {
      if (prev.some((it) => it.productId === p.id)) {
        return prev.map((it) =>
          it.productId === p.id ? { ...it, qty: it.qty + 1 } : it
        );
      }
      return [...prev, productToCartItem(p)];
    });
    setLines((prev) => {
      const existing = prev.find((row) => row.productId === p.id);
      if (existing) {
        return prev.map((row) =>
          row.productId === p.id ? { ...row, qty: row.qty + 1, selected: true } : row
        );
      }
      return [
        ...prev,
        {
          productId: p.id,
          selected: true,
          from: CENTRAL_WAREHOUSE,
          qty: 1,
        },
      ];
    });
    setQ("");
    setHits([]);
  }

  function removeProduct(productId: string) {
    setItems((prev) => prev.filter((it) => it.productId !== productId));
    setLines((prev) => prev.filter((row) => row.productId !== productId));
  }

  async function openStock(item: CartItem) {
    setStockFor({ productId: item.productId, name: item.name, loading: true });
    if (USE_MOCK) {
      setStockFor({ productId: item.productId, name: item.name, loading: false, warehouses: [] });
      return;
    }
    try {
      const data = await api.get<{ warehouses: WarehouseStockLine[] }>(
        `/catalog/${encodeURIComponent(item.productId)}/stock`
      );
      setStockFor({
        productId: item.productId,
        name: item.name,
        loading: false,
        warehouses: data.warehouses ?? [],
      });
    } catch {
      setStockFor({
        productId: item.productId,
        name: item.name,
        loading: false,
        error: "Не удалось загрузить остатки",
      });
    }
  }

  async function submit() {
    const chosen = lines.filter((row) => row.selected && row.qty > 0);
    if (chosen.length === 0) return;
    if (chosen.some((row) => row.from === target)) {
      setError("«Откуда» и «Куда» не должны совпадать у выбранных позиций");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let activeDealNumber = dealNumber;
      let activeStore = store;
      if (ensureDeal) {
        const resolved = await ensureDeal();
        if (!resolved) {
          setError("Сначала заполните обязательные поля заявки");
          return;
        }
        activeDealNumber = resolved.dealNumber;
        activeStore = resolved.store;
      }

      // Группируем по источнику — одна задача на каждое «откуда».
      const bySource = new Map<string, LinePlan[]>();
      for (const row of chosen) {
        bySource.set(row.from, [...(bySource.get(row.from) ?? []), row]);
      }
      const summaries: string[] = [];
      for (const [from, group] of bySource) {
        const positions = group.map((row) => {
          const item = items.find((it) => it.productId === row.productId)!;
          return { productId: row.productId, quantity: row.qty, name: item.name };
        });
        if (!USE_MOCK) {
          const fromStoreMsId = warehouseId(from);
          const toStoreMsId = warehouseId(target);
          if (!fromStoreMsId || !toStoreMsId) {
            setError("Не найдены склады МойСклад — обновите справочники");
            return;
          }
          const ids = positions.map((p) => p.productId).sort().join(",");
          const keyPrefix = activeDealNumber != null ? `deal-move:${activeDealNumber}` : "free-move";
          await api.post("/tasks/movement", {
            kind: "movement",
            idempotencyKey: shortIdempotencyKey(keyPrefix, ids, `${from}->${target}`, String(Date.now())),
            title: taskTitle("movement", activeDealNumber),
            store: activeStore,
            assigneeRole: isCentralWarehouse(from) ? "logist" : "consultant",
            ...(activeDealNumber != null ? { dealNumber: activeDealNumber } : {}),
            fromStoreMsId,
            toStoreMsId,
            positions: positions.map((p) => ({
              productId: p.productId,
              quantity: p.quantity,
              name: p.name,
            })),
            metadata: { from, to: target },
          });
        }
        summaries.push(`${from} → ${target}: ${positions.map((p) => p.name).join(", ")}`);
      }
      setSuccess(summaries.join("; "));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось поставить задачу на перемещение");
    } finally {
      setBusy(false);
    }
  }

  const selectedCount = lines.filter((row) => row.selected).length;

  return (
    <>
      <Modal
        open={open}
        onClose={success ? finishSuccess : onClose}
        title={success ? "Готово" : "Перемещение товара"}
        wide
      >
        {success ? (
          <div className="py-4 text-center space-y-4">
            <div className="mx-auto w-14 h-14 rounded-full bg-emerald-400/15 grid place-items-center">
              <CheckCircle2 size={28} className="text-emerald-300" />
            </div>
            <div>
              <div className="text-white text-lg font-bold">Перемещение запущено</div>
              <div className="text-mute text-sm mt-2 max-w-md mx-auto break-words">{success}</div>
              <div className="text-mute text-[12px] mt-3">
                Задача появилась в очереди логиста или консультанта источника
              </div>
            </div>
            <div className="flex justify-center">
              <Button onClick={finishSuccess}>Готово</Button>
            </div>
          </div>
        ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
            <ArrowRightLeft size={15} className="text-gold" />
            {allowCatalogPick
              ? "Добавьте товар и укажите откуда везти"
              : "Выберите позиции и откуда везти каждую"}
          </div>

          {allowCatalogPick && (
            <div className="space-y-2">
              <label className="block">
                <div className="field-label">Поиск товара</div>
                <input
                  className="input"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Артикул, цвет, штрихкод…"
                  autoFocus
                />
              </label>
              {searching && (
                <div className="flex items-center gap-2 text-mute text-sm px-1">
                  <Loader2 size={14} className="animate-spin" /> Ищем…
                </div>
              )}
              {hits.length > 0 && (
                <div className="rounded-lg border border-ink-700 divide-y divide-ink-800 max-h-48 overflow-y-auto">
                  {hits.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className="w-full text-left px-3 py-2 hover:bg-ink-800/80 flex items-center gap-2"
                      onClick={() => addProduct(p)}
                    >
                      <Plus size={14} className="text-gold shrink-0" />
                      <span className="text-[13px] text-white min-w-0">
                        <span className="break-words">{p.name}</span>
                        {p.sku && (
                          <span className="block text-[11px] text-mute mt-0.5">{p.sku}</span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="rounded-lg border border-ink-700 divide-y divide-ink-800 overflow-hidden">
            {items.map((item) => {
              const line = lines.find((row) => row.productId === item.productId);
              if (!line) return null;
              return (
                <div key={item.productId} className="px-3 py-2.5 space-y-2 bg-ink-900/40">
                  <div className="flex items-start gap-2.5">
                    {!allowCatalogPick && (
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={line.selected}
                        onChange={(e) => patchLine(item.productId, { selected: e.target.checked })}
                      />
                    )}
                    <button
                      type="button"
                      className="flex-1 text-left min-w-0"
                      onClick={() => void openStock(item)}
                      title="Остатки по складам"
                    >
                      <div className="text-[14px] text-white break-words leading-snug hover:text-gold-soft">
                        {item.name}
                      </div>
                      <div className="text-[12px] text-mute mt-0.5">× {line.qty} · нажмите для остатков</div>
                    </button>
                    <input
                      className="input w-16 text-center py-1.5 text-sm"
                      inputMode="numeric"
                      disabled={!line.selected}
                      value={line.qty}
                      onChange={(e) =>
                        patchLine(item.productId, {
                          qty: Math.max(
                            1,
                            Math.min(
                              allowCatalogPick ? 999 : item.qty,
                              Number(e.target.value.replace(/\D/g, "")) || 1
                            )
                          ),
                        })
                      }
                    />
                    {allowCatalogPick && (
                      <button
                        type="button"
                        className="mt-1 p-1.5 text-mute hover:text-red-300"
                        title="Убрать"
                        onClick={() => removeProduct(item.productId)}
                      >
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                  {line.selected && (
                    <label className={`block ${allowCatalogPick ? "" : "pl-7"}`}>
                      <div className="field-label">Откуда</div>
                      <Select
                        size="sm"
                        value={line.from}
                        onChange={(next) => patchLine(item.productId, { from: next })}
                        options={opts(...sourceOptions)}
                      />
                    </label>
                  )}
                </div>
              );
            })}
            {items.length === 0 && (
              <div className="px-3 py-6 text-center text-mute text-sm">
                {allowCatalogPick ? "Найдите и добавьте товар выше" : "В заявке нет товаров"}
              </div>
            )}
          </div>

          <label className="block">
            <div className="field-label">Куда (магазин получатель)</div>
            <Select value={target} onChange={setTarget} options={opts(...MOVEMENT_TARGETS)} />
          </label>

          <div className="hint-only text-[12px] text-mute">
            Разные «откуда» → отдельные задачи (ЦС — логисту, шоурум — консультанту источника).
          </div>
          {error && <div className="text-[13px] text-red-300">{error}</div>}

          <div className="flex justify-end gap-2">
            <Button variant="subtle" onClick={onClose}>
              Отмена
            </Button>
            <Button disabled={busy || selectedCount === 0} onClick={() => void submit()}>
              {busy ? "Ставим…" : `Запустить · ${selectedCount}`}
            </Button>
          </div>
        </div>
        )}
      </Modal>

      <Modal
        open={!!stockFor}
        onClose={() => setStockFor(null)}
        title={stockFor ? `Остатки · ${stockFor.name}` : "Остатки"}
        wide
      >
        {stockFor?.loading && (
          <div className="flex items-center gap-2 text-mute text-sm py-6 justify-center">
            <Loader2 size={16} className="animate-spin" /> Загрузка…
          </div>
        )}
        {stockFor?.error && <div className="text-amber-300/90 text-sm py-4 text-center">{stockFor.error}</div>}
        {stockFor?.warehouses && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-mute border-b border-ink-700">
                <th className="pb-2 font-medium">Склад</th>
                <th className="pb-2 font-medium text-right">Доступно</th>
                <th className="pb-2 font-medium text-right">Резерв</th>
                <th className="pb-2 font-medium text-right">Остаток</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {stockFor.warehouses.map((w) => (
                <tr key={w.warehouseMsId}>
                  <td className="py-2 text-white">{w.name}</td>
                  <td className="py-2 text-right text-emerald-300/90 font-semibold">{fmtQty(w.available)}</td>
                  <td className="py-2 text-right text-mute">{fmtQty(w.reserve)}</td>
                  <td className="py-2 text-right text-white">{fmtQty(w.stock)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Modal>
    </>
  );
}
