import { Fragment, useEffect, useState } from "react";
import { ArrowRightLeft, ChevronDown, ChevronRight, Copy } from "lucide-react";
import {
  CENTRAL_WAREHOUSE,
  findWarehouseId,
  isCentralWarehouse,
  shortIdempotencyKey,
  SUIT_PART_LABEL,
  taskTitle,
} from "@kassa/shared";
import type { SuitModel, SuitOrphan, SuitPart } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { money } from "../lib/format";
import { Button } from "./ui";

/** Модель костюма в поиске: комплектность как на старом экране «Костюмы», плюс цена из матрицы. */
export interface PricedSuitModel extends SuitModel {
  priceRub?: number | null;
}

function partsLine(parts: Record<SuitPart, number>): string {
  return (["jacket", "trousers", "vest"] as SuitPart[])
    .filter((part) => parts[part] > 0)
    .map((part) => `${SUIT_PART_LABEL[part]} ${parts[part]}`)
    .join(" · ");
}

function orphanLine(orphan: SuitOrphan): string {
  const missing = orphan.missing.map((part) => SUIT_PART_LABEL[part]).join(" и ");
  const nearest =
    orphan.nearestSizes.length > 0 ? `, ближайшие размеры ${orphan.nearestSizes.join(", ")}` : "";
  return `${SUIT_PART_LABEL[orphan.part]} ${orphan.qty} шт. без ${missing || "пары"}${nearest}`;
}

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

  async function copyVariation() {
    try {
      await navigator.clipboard.writeText(model.variation);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="card p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <button type="button" onClick={onToggle} className="text-mute hover:text-white shrink-0">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <button type="button" onClick={onToggle} className="flex-1 min-w-0 text-left">
          <div className="text-white font-semibold truncate">
            {model.title}
            {model.mixed && (
              <span className="ml-2 chip bg-ink-800 text-amber-300 text-[11px]">цвет по пиджаку</span>
            )}
          </div>
          <div className="text-[12px] text-mute truncate">
            {model.variation}
            {model.height ? ` · ростовка ${model.height}` : ""}
            {model.onHalfSetWarehouse > 0 ? ` · на складе полупарков ${model.onHalfSetWarehouse}` : ""}
          </div>
        </button>
        <button
          type="button"
          onClick={() => void copyVariation()}
          title="Скопировать код вариации для поиска в МойСкладе"
          className="text-mute hover:text-gold shrink-0"
        >
          <Copy size={15} />
        </button>
        {copied && <span className="text-[11px] text-emerald-300 shrink-0">скопировано</span>}
        {model.priceRub != null && (
          <span className="text-gold-soft font-semibold whitespace-nowrap shrink-0 hidden sm:inline">
            {money(model.priceRub)}
          </span>
        )}
        <div className="hidden sm:flex items-center gap-3 text-[13px] shrink-0">
          <span className="text-emerald-300 font-semibold">{model.whole}</span>
          <span className="text-amber-300 font-semibold">{model.tolerant}</span>
          <span className="text-red-300 font-semibold">{model.orphans}</span>
        </div>
      </div>

      {open && (
        <div className="border-t border-ink-800 divide-y divide-ink-800">
          {model.priceRub != null && (
            <div className="px-4 py-2 text-[13px] text-gold-soft sm:hidden">{money(model.priceRub)}</div>
          )}
          {model.sizes.map((size) => (
            <div key={size.size} className="px-4 py-3 space-y-2">
              <div className="flex flex-wrap items-center gap-3 text-[13px]">
                <span className="text-white font-semibold">Размер {size.size}</span>
                <span className="text-mute">{partsLine(size.parts)}</span>
                {size.whole > 0 && <span className="text-emerald-300">цельных {size.whole}</span>}
                {size.tolerant > 0 && <span className="text-amber-300">в допуске {size.tolerant}</span>}
              </div>
              {size.orphans.map((orphan) => (
                <Fragment key={`${orphan.part}-${size.size}`}>
                  <div className="text-[12px] text-red-200">{orphanLine(orphan)}</div>
                  {orphan.pairLocations.length > 0 && (
                    <AssembleSuit model={model} size={size.size} orphan={orphan} target={warehouse} />
                  )}
                </Fragment>
              ))}
            </div>
          ))}
          {model.sizes.length === 0 && (
            <div className="px-4 py-2.5 text-[13px] text-mute">Ни одного размера в остатке.</div>
          )}
        </div>
      )}
    </div>
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
          onClick={() => void assemble(location)}
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
