import { useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, CheckCircle2, Loader2, Plus, ScanLine, Search, Trash2 } from "lucide-react";
import {
  CENTRAL_WAREHOUSE,
  ITEM_LOCATIONS,
  MOVEMENT_TARGETS,
  STORE_TO_WAREHOUSE,
  findWarehouseId,
  isCentralWarehouse,
  shortIdempotencyKey,
  taskTitle,
  type Product,
} from "@kassa/shared";
import type { CartItem } from "../data/types";
import { api, USE_MOCK } from "../api/client";
import { money } from "../lib/format";
import { Button, Modal, Select, opts } from "./ui";
import { BarcodeScannerModal } from "./BarcodeScanner";
import { groupLocations } from "../lib/selectGroups";

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
  /** Вид заявки, если уже известен. Иначе модалка сама читает заявку по номеру. */
  dealKind?: string;
}) {
  const seedItems = useMemo(() => (initialItems ?? EMPTY_ITEMS).filter((item) => item.productId !== "rent"), [initialItems]);
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
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const warehouseName =
    (STORE_TO_WAREHOUSE as Record<string, string | null>)[store] ?? store;
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
    // Только при открытии. Повтор по seedItems/store стирал бы запрос,
    // который консультант уже набирает.
    setError(null);
    setSuccess(null);
    setSearchError(null);
    setNotice(null);
    setScannerOpen(false);
    setSearchOpen(false);
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
    // seedItems, store и defaultTarget читаем на момент открытия.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || !allowCatalogPick) return;
    const query = q.trim();
    if (!query) {
      setHits([]);
      setSearching(false);
      setSearchError(null);
      return;
    }
    if (USE_MOCK) {
      setHits([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    setSearchError(null);
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          const rows = await api.get<Product[]>(
            `/catalog/search?q=${encodeURIComponent(query)}&store=${encodeURIComponent(store)}&limit=30`
          );
          if (cancelled) return;
          setHits(Array.isArray(rows) ? rows : []);
        } catch (err) {
          if (cancelled) return;
          setHits([]);
          setSearchError(err instanceof Error && err.message ? err.message : "Не удалось найти товар");
        } finally {
          if (!cancelled) setSearching(false);
        }
      })();
    }, 300);
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
    setSearchOpen(false);
    setSearchError(null);
  }

  function flash(msg: string) {
    setNotice(msg);
    window.setTimeout(() => setNotice((cur) => (cur === msg ? null : cur)), 4000);
  }

  async function handleScanned(code: string) {
    setScannerOpen(false);
    try {
      const list = USE_MOCK
        ? []
        : await api.get<Product[]>(
            `/catalog/search?q=${encodeURIComponent(code)}&store=${encodeURIComponent(store)}&limit=5`
          );
      const rows = Array.isArray(list) ? list : [];
      if (rows.length === 1) {
        addProduct(rows[0]!);
        flash(`Добавлено: ${rows[0]!.name}`);
      } else if (rows.length > 1) {
        setQ(code);
        setHits(rows);
        setSearchOpen(true);
        setNotice(null);
      } else {
        flash(`Товар со штрихкодом «${code}» не найден в каталоге`);
      }
    } catch {
      flash("Не удалось выполнить поиск по штрихкоду");
    }
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
                Задача появилась в очереди
              </div>
            </div>
            <div className="flex justify-center">
              <Button onClick={finishSuccess}>Готово</Button>
            </div>
          </div>
        ) : (
        <div className="space-y-4">
          {scannerOpen && (
            <BarcodeScannerModal onDetected={(code) => void handleScanned(code)} onClose={() => setScannerOpen(false)} />
          )}
          <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
            <ArrowRightLeft size={15} className="text-gold" />
            {allowCatalogPick
              ? "Добавьте товар и укажите откуда везти"
              : "Выберите позиции и откуда везти каждую"}
          </div>

          {allowCatalogPick && notice && (
            <div className="rounded-lg border border-gold/30 bg-gold/10 text-gold-soft text-[13px] px-3 py-2">
              {notice}
            </div>
          )}

          {allowCatalogPick && (
            <div className="space-y-2">
              <div className="field-label">Поиск товара</div>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
                  <input
                    className="input pl-9"
                    value={q}
                    autoFocus
                    placeholder="Вариация, артикул, цвет или штрихкод…"
                    onFocus={() => setSearchOpen(true)}
                    onChange={(e) => {
                      setQ(e.target.value);
                      setSearchOpen(true);
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
              {searchOpen && q.trim() && (
                <div className="card p-1 max-h-64 overflow-y-auto">
                  {searching && (
                    <div className="px-3 py-3 text-mute text-sm flex items-center gap-2">
                      <Loader2 size={14} className="animate-spin" /> Поиск…
                    </div>
                  )}
                  {!searching && searchError && (
                    <div className="px-3 py-3 text-amber-300/90 text-sm">{searchError}</div>
                  )}
                  {!searching && !searchError && hits.length === 0 && (
                    <div className="px-3 py-3 text-mute text-sm">Ничего не найдено</div>
                  )}
                  {!searching &&
                    !searchError &&
                    hits.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => addProduct(p)}
                        className="w-full text-left px-3 py-2 rounded-lg hover:bg-ink-800 flex items-center gap-3"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-[14px] text-white break-words whitespace-normal leading-snug">{p.name}</div>
                          <div className="text-[12px] text-mute">
                            {[p.sku, `доступно ${fmtQty(p.stock)}`].filter(Boolean).join(" · ")}
                          </div>
                        </div>
                        <div className="text-gold-soft font-semibold shrink-0">{money(p.price)}</div>
                        <Plus size={16} className="text-gold shrink-0" />
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
                        groups={groupLocations(sourceOptions)}
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
                {stockFor.warehouses.map((w) => {
                  const isCurrent = w.name === warehouseName;
                  return (
                    <tr key={w.warehouseMsId} className={isCurrent ? "bg-gold/10" : undefined}>
                      <td className="py-2.5 pr-2 text-white break-words whitespace-normal">
                        {w.name}
                        {isCurrent && (
                          <span className="ml-1.5 text-[11px] text-gold-soft whitespace-nowrap">текущий</span>
                        )}
                      </td>
                      <td className="py-2.5 px-1 text-right font-semibold text-emerald-300/90">{fmtQty(w.available)}</td>
                      <td className="py-2.5 px-1 text-right text-mute-soft">{fmtQty(w.reserve)}</td>
                      <td className="py-2.5 pl-1 text-right text-white">{fmtQty(w.stock)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="hint-only mt-3 text-[12px] text-mute">
              Доступно = остаток − резерв. Склады с МойСклад. СДЭК касса считает сама: товар в доставке клиенту, это не «В пути» между своими складами.
            </p>
          </div>
        )}
      </Modal>
    </>
  );
}
