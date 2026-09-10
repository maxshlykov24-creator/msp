import { useEffect, useState } from "react";
import { ArrowRightLeft, ChevronDown, ChevronRight, Copy } from "lucide-react";
import {
  CENTRAL_WAREHOUSE,
  findWarehouseId,
  isCentralWarehouse,
  shortIdempotencyKey,
  SUIT_PART_LABEL,
  taskTitle,
} from "@kassa/shared";
import type { SuitModel, SuitOrphan, SuitSizeRow } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { money } from "../lib/format";
import {
  EXPANDED_COLUMNS,
  MAIN_WAREHOUSES,
  fmtQty,
  leftoverStock,
  qtyAt,
  qtyClass,
} from "../lib/stockDisplay";
import { Button } from "./ui";

/** Модель костюма в поиске: остаток по складам, плюс цена из матрицы кассы. */
export interface PricedSuitModel extends SuitModel {
  priceRub?: number | null;
}

type SizeDetail = "stores" | "all";

export function SuitModelRow({
  model,
  open,
  onToggle,
  warehouse,
}: {
  model: PricedSuitModel;
  open: boolean;
  onToggle: () => void;
  warehouse: string;
}) {
  const [copied, setCopied] = useState(false);
  const [sizeOpen, setSizeOpen] = useState<{ size: string; detail: SizeDetail } | null>(null);

  useEffect(() => {
    if (!open) setSizeOpen(null);
  }, [open]);

  const metaBits = [model.variation, model.height, model.fit].filter(Boolean);

  async function copyVariation() {
    try {
      await navigator.clipboard.writeText(model.variation);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  function toggleSize(size: string) {
    setSizeOpen((prev) => {
      if (!prev || prev.size !== size) return { size, detail: "stores" };
      if (prev.detail === "stores") return { size, detail: "all" };
      return null;
    });
  }

  return (
    <div className="card p-0 overflow-hidden">
      <div className="flex items-stretch gap-3 px-4 py-3">
        <button type="button" onClick={onToggle} className="text-mute hover:text-white shrink-0 self-center">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <button type="button" onClick={onToggle} className="flex-1 min-w-0 text-left self-center">
          <div className="text-white font-semibold truncate">{model.title}</div>
        </button>
        <div className="hidden sm:block w-[168px] shrink-0 self-center border-l border-ink-700 pl-3">
          <div className="text-white text-[13px] font-medium truncate" title={model.variation}>
            {model.variation || "—"}
          </div>
          <div className="text-[12px] text-mute truncate">
            {[model.height, model.fit].filter(Boolean).join(" · ") || "—"}
          </div>
        </div>
        <button
          type="button"
          onClick={() => void copyVariation()}
          title="Скопировать код вариации для поиска в МойСкладе"
          className="text-mute hover:text-gold shrink-0 self-center"
        >
          <Copy size={15} />
        </button>
        {copied && <span className="text-[11px] text-emerald-300 shrink-0 self-center">скопировано</span>}
        {model.priceRub != null && (
          <span className="text-gold-soft font-semibold whitespace-nowrap shrink-0 hidden sm:inline self-center">
            {money(model.priceRub)}
          </span>
        )}
        <div className="min-w-[3.25rem] text-right shrink-0 self-center">
          <span className={`text-[22px] leading-none font-extrabold tabular-nums ${qtyClass(model.whole)}`}>
            {fmtQty(model.whole)}
          </span>
        </div>
      </div>
      <div className="sm:hidden px-4 pb-3 -mt-1 flex items-center justify-between gap-3 text-[12px] text-mute">
        <span className="truncate">{metaBits.join(" · ") || "—"}</span>
        {model.priceRub != null && <span className="text-gold-soft font-semibold">{money(model.priceRub)}</span>}
      </div>

      {open && (
        <div className="border-t border-ink-800 overflow-x-auto">
          <div className="hint-only px-4 pt-3 text-[12px] text-mute">
            Нажмите размер: сначала цельные на трёх складах, ещё раз — все положения.
          </div>
          <table className="w-full text-sm min-w-[520px]">
            <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Размер</th>
                {sizeOpen &&
                  MAIN_WAREHOUSES.map((w) => (
                    <th key={w.id} className="text-right px-3 py-2 font-medium whitespace-nowrap w-[120px]">
                      {w.label}
                    </th>
                  ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {model.sizes.map((size) => (
                <SizeStockRows
                  key={size.size}
                  model={model}
                  size={size}
                  warehouse={warehouse}
                  showStores={Boolean(sizeOpen)}
                  detail={sizeOpen?.size === size.size ? sizeOpen.detail : null}
                  onToggle={() => toggleSize(size.size)}
                />
              ))}
              {model.sizes.length === 0 && (
                <tr>
                  <td colSpan={sizeOpen ? 4 : 1} className="px-4 py-2.5 text-[13px] text-mute">
                    Ни одного размера в остатке.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SizeStockRows({
  model,
  size,
  warehouse,
  showStores,
  detail,
  onToggle,
}: {
  model: PricedSuitModel;
  size: SuitSizeRow;
  warehouse: string;
  showStores: boolean;
  detail: SizeDetail | null;
  onToggle: () => void;
}) {
  const stores = size.warehouses ?? [];
  const leftovers = detail === "all" ? leftoverStock(stores.filter((w) => w.whole > 0)) : [];
  const assembleOrphans = size.orphans.filter((orphan) => orphan.pairLocations.length > 0);
  const selected = detail != null;

  return (
    <>
      <tr
        className={`hover:bg-ink-800/40 cursor-pointer ${selected ? "bg-ink-800/30" : ""}`}
        onClick={onToggle}
      >
        <td className="px-4 py-3 align-middle">
          <div className="flex items-center gap-2">
            {selected ? (
              <ChevronDown size={15} className="text-mute shrink-0" />
            ) : (
              <ChevronRight size={15} className="text-mute shrink-0" />
            )}
            <span className="text-white font-semibold">{size.size}</span>
            <span className={`ml-auto text-[18px] font-extrabold tabular-nums ${qtyClass(size.whole)}`}>
              {fmtQty(size.whole)}
            </span>
          </div>
        </td>
        {showStores &&
          MAIN_WAREHOUSES.map((w) => {
            const n = qtyAt(stores, w.match);
            return (
              <td key={w.id} className={`px-3 py-3 text-right tabular-nums align-middle ${qtyClass(n)}`}>
                {fmtQty(n)}
              </td>
            );
          })}
      </tr>
      {detail === "all" && (
        <tr className="bg-ink-900/60">
          <td colSpan={showStores ? 4 : 1} className="px-4 py-3">
            <div className="field-label mb-2">Все склады и положения</div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {EXPANDED_COLUMNS.map((col) => (
                <div key={col.id} className="space-y-1.5">
                  {col.slots.map((slot) => {
                    const n = qtyAt(stores, slot.match);
                    return (
                      <div
                        key={slot.label}
                        className="flex justify-between gap-3 rounded-md border border-ink-700 px-2.5 py-1.5 text-[13px]"
                      >
                        <span className="text-mute-soft truncate">{slot.label}</span>
                        <span className={`tabular-nums shrink-0 ${qtyClass(n)}`}>{fmtQty(n)}</span>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            {leftovers.length > 0 && (
              <div className="mt-3 grid sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
                {leftovers.map((w) => (
                  <div
                    key={w.name}
                    className="flex justify-between gap-3 rounded-md border border-ink-700 px-2.5 py-1.5 text-[13px]"
                  >
                    <span className="text-mute-soft truncate">{w.name}</span>
                    <span className={`tabular-nums shrink-0 ${qtyClass(w.whole)}`}>{fmtQty(w.whole)}</span>
                  </div>
                ))}
              </div>
            )}
            {assembleOrphans.length > 0 && (
              <div className="mt-3 space-y-2">
                {assembleOrphans.map((orphan) => (
                  <AssembleSuit
                    key={`${orphan.part}-${size.size}`}
                    model={model}
                    size={size.size}
                    orphan={orphan}
                    target={warehouse}
                  />
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function AssembleSuit({
  model,
  size,
  orphan,
  target,
}: {
  model: SuitModel;
  size: string;
  orphan: SuitOrphan;
  target: string;
}) {
  const { activeStore } = useStore();
  const [warehouses, setWarehouses] = useState<Array<{ id: string; name: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (USE_MOCK) return;
    api
      .get<{ warehouses: Array<{ id: string; name: string }> }>("/catalog/references")
      .then((refs) => setWarehouses(refs.warehouses ?? []))
      .catch(() => {});
  }, []);

  const destination = target || activeStore;

  async function assemble(location: SuitOrphan["pairLocations"][number]) {
    setBusy(true);
    setError(null);
    try {
      const fromStoreMsId = findWarehouseId(warehouses, location.warehouse);
      const toStoreMsId = findWarehouseId(warehouses, destination);
      if (!fromStoreMsId || !toStoreMsId) {
        setError("Не найдены склады МойСклад — обновите справочники");
        return;
      }
      await api.post("/tasks/movement", {
        kind: "movement",
        idempotencyKey: shortIdempotencyKey(
          `suit-assemble:${model.modelId}`,
          `${location.msId}:${size}`,
          `${location.warehouse}->${destination}`
        ),
        title: taskTitle("movement"),
        store: destination,
        assigneeRole: isCentralWarehouse(location.warehouse) ? "logist" : "consultant",
        fromStoreMsId,
        toStoreMsId,
        positions: [
          {
            productId: location.msId,
            quantity: 1,
            name: `${SUIT_PART_LABEL[location.part]} ${model.variation} размер ${location.size}`,
          },
        ],
        metadata: {
          from: location.warehouse,
          to: destination,
          reason: "Сбор костюма из полупарка",
          variation: model.variation,
          size,
        },
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось поставить перемещение");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return <div className="text-[12px] text-emerald-300">Перемещение поставлено в задачи.</div>;
  }

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {orphan.pairLocations.slice(0, 4).map((location) => (
        <Button
          key={`${location.msId}-${location.warehouse}`}
          variant="outline"
          className="py-1.5 px-3 text-[12px]"
          disabled={busy || USE_MOCK}
          onClick={(e) => {
            e.stopPropagation();
            void assemble(location);
          }}
        >
          <ArrowRightLeft size={13} />
          {SUIT_PART_LABEL[location.part]} {location.size} · {location.warehouse}
          {location.warehouse === CENTRAL_WAREHOUSE ? " (логист)" : ""}
        </Button>
      ))}
      {error && <span className="text-[12px] text-red-300">{error}</span>}
    </div>
  );
}
