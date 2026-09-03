import { useEffect, useState } from "react";
import { ArrowRightLeft } from "lucide-react";
import {
  CENTRAL_WAREHOUSE,
  ITEM_LOCATIONS,
  MOVEMENT_TARGETS,
  findWarehouseId,
  isCentralWarehouse,
  shortIdempotencyKey,
  taskTitle,
} from "@kassa/shared";
import type { CartItem } from "../data/types";
import { api, USE_MOCK } from "../api/client";
import { Button } from "./ui";

interface WarehouseRef {
  id: string;
  name: string;
}

/**
 * Заявка на перемещение по этапу «Ждет товар» (п.15): консультант отмечает позиции,
 * куда и откуда везти. Источник «Центральный склад» уводит задачу в очередь логиста,
 * любой другой — в очередь консультанта магазина-источника.
 */
export function MovementRequest({
  dealNumber,
  store,
  items,
  onCreated,
}: {
  dealNumber: number;
  store: string;
  items: CartItem[];
  onCreated?: (summary: string) => void;
}) {
  const [warehouses, setWarehouses] = useState<WarehouseRef[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [target, setTarget] = useState(MOVEMENT_TARGETS[0]!);
  const [source, setSource] = useState(CENTRAL_WAREHOUSE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (USE_MOCK) return;
    api
      .get<{ warehouses: WarehouseRef[] }>("/catalog/references")
      .then((refs) => setWarehouses(refs.warehouses ?? []))
      .catch(() => {});
  }, []);

  function warehouseId(name: string): string | undefined {
    return findWarehouseId(warehouses, name);
  }

  function toggle(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    setDone(false);
  }

  async function submit() {
    if (selected.length === 0 || source === target) return;
    setBusy(true);
    setError(null);
    try {
      const chosen = items.filter((item) => selected.includes(item.productId));
      if (!USE_MOCK) {
        const fromStoreMsId = warehouseId(source);
        const toStoreMsId = warehouseId(target);
        if (!fromStoreMsId || !toStoreMsId) {
          setError("Не найдены склады МойСклад — обновите справочники");
          return;
        }
        await api.post("/tasks/movement", {
          kind: "movement",
          idempotencyKey: shortIdempotencyKey(
            `deal-move:${dealNumber}`,
            selected.slice().sort().join(","),
            `${source}->${target}`
          ),
          title: taskTitle("movement", dealNumber),
          store,
          assigneeRole: isCentralWarehouse(source) ? "logist" : "consultant",
          dealNumber,
          fromStoreMsId,
          toStoreMsId,
          positions: chosen.map((item) => ({
            productId: item.productId,
            quantity: item.qty,
            name: item.name,
          })),
          metadata: { from: source, to: target },
        });
      }
      setDone(true);
      onCreated?.(`Перемещение ${source} → ${target}: ${chosen.map((item) => item.name).join(", ")}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось поставить задачу на перемещение");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
        <ArrowRightLeft size={15} className="text-gold" /> Перемещение товара под заявку
      </div>
      <div className="space-y-1.5">
        {items.map((item) => (
          <label key={item.productId} className="flex items-center gap-2.5 text-sm text-mute-soft">
            <input
              type="checkbox"
              checked={selected.includes(item.productId)}
              onChange={() => toggle(item.productId)}
            />
            <span className="flex-1 min-w-0 break-words">{item.name} × {item.qty}</span>
          </label>
        ))}
      </div>
      <div className="grid sm:grid-cols-2 gap-3">
        <label>
          <div className="field-label">Откуда</div>
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
            {ITEM_LOCATIONS.filter((name) => !name.startsWith("СДЭК")).map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          <div className="field-label">Куда</div>
          <select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>
            {MOVEMENT_TARGETS.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="text-[12px] text-mute">
        {isCentralWarehouse(source)
          ? "Задача уйдёт в очередь логиста"
          : `Задача уйдёт в очередь консультанта: ${source}`}
      </div>
      {error && <div className="text-[12px] text-red-300">{error}</div>}
      <div className="flex justify-end">
        <Button disabled={busy || done || selected.length === 0 || source === target} onClick={() => void submit()}>
          {done ? "Задача поставлена" : "Запустить перемещение"}
        </Button>
      </div>
    </div>
  );
}
