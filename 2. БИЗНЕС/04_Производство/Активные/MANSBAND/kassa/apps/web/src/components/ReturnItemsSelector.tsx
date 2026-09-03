import { useEffect, useRef, useState } from "react";
import { Check, ScanLine } from "lucide-react";
import type { CartItem } from "../data/types";
import { money } from "../lib/format";
import { BarcodeScannerModal } from "./BarcodeScanner";
import { Button } from "./ui";

function keyOf(item: CartItem, index: number): string {
  return `${item.productId || "no-barcode"}:${index}`;
}

function codesOf(item: CartItem): string[] {
  const out: string[] = [];
  if (item.barcode?.trim()) out.push(item.barcode.trim().toLowerCase());
  if (item.productId?.trim()) out.push(item.productId.trim().toLowerCase());
  return out;
}

/**
 * Выбор позиций из исходной продажи для возврата/обмена.
 * С штрихкодом — только через скан (пиканье); без штрихкода — вручную + задача на проверку.
 */
export function ReturnItemsSelector({
  original,
  selected,
  onChange,
  onManualWithoutBarcode,
}: {
  original: CartItem[];
  selected: CartItem[];
  onChange: (items: CartItem[]) => void;
  onManualWithoutBarcode?: (keys: string[]) => void;
}) {
  const [scannerOpen, setScannerOpen] = useState(false);
  const [manualCode, setManualCode] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);

  const selectedKeys = new Set(
    selected.map((selectedItem) => {
      const index = original.findIndex(
        (item) => item.productId === selectedItem.productId && item.name === selectedItem.name
      );
      return keyOf(selectedItem, Math.max(0, index));
    })
  );

  useEffect(() => {
    const t = window.setTimeout(() => scanRef.current?.focus(), 120);
    return () => window.clearTimeout(t);
  }, [original.length, selected.length]);

  function emitManual(nextSelected: CartItem[]) {
    const keys = new Set(
      nextSelected.map((row) => {
        const index = original.findIndex(
          (item) => item.productId === row.productId && item.name === row.name
        );
        return keyOf(row, Math.max(0, index));
      })
    );
    const manual = original
      .map((row, i) => ({ row, key: keyOf(row, i) }))
      .filter(({ row, key }) => !row.barcode?.trim() && keys.has(key))
      .map(({ key }) => key);
    onManualWithoutBarcode?.(manual);
  }

  function selectAt(index: number, fromScan: boolean) {
    const item = original[index];
    if (!item) return;
    const key = keyOf(item, index);
    if (selectedKeys.has(key)) {
      setNotice(`Уже выбрано: ${item.name}`);
      setError(null);
      return;
    }
    const next = [...selected, { ...item, qty: item.qty }];
    onChange(next);
    emitManual(next);
    setNotice(fromScan ? `Сошлось: ${item.name}` : `Выбрано вручную: ${item.name}`);
    setError(null);
  }

  function deselectAt(index: number) {
    const item = original[index];
    if (!item) return;
    const next = selected.filter(
      (row) => !(row.productId === item.productId && row.name === item.name)
    );
    onChange(next);
    emitManual(next);
    setNotice(`Снято: ${item.name}`);
    setError(null);
  }

  function applyScan(raw: string) {
    const code = raw.trim();
    if (!code) return;
    setManualCode("");
    const normalized = code.toLowerCase();
    const index = original.findIndex((item) => codesOf(item).includes(normalized));
    if (index < 0) {
      setError(`Штрихкод «${code}» не из этой продажи`);
      setNotice(null);
      return;
    }
    selectAt(index, true);
  }

  function tryManualPick(item: CartItem, index: number) {
    if (item.barcode?.trim()) {
      setError("Отсканируйте штрихкод позиции — так сверяем возврат");
      setNotice(null);
      scanRef.current?.focus();
      return;
    }
    if (selectedKeys.has(keyOf(item, index))) {
      deselectAt(index);
      return;
    }
    selectAt(index, false);
  }

  if (original.length === 0) {
    return (
      <div className="rounded-lg border border-ink-700 px-3 py-3 text-[13px] text-mute">
        В исходной заявке нет позиций для возврата.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {scannerOpen && (
        <BarcodeScannerModal
          onDetected={(code) => {
            setScannerOpen(false);
            applyScan(code);
          }}
          onClose={() => setScannerOpen(false)}
        />
      )}

      <div className="space-y-2">
        <div className="field-label">Сканирование для перепроверки</div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={scanRef}
            className="input flex-1 min-w-[12rem]"
            value={manualCode}
            onChange={(e) => setManualCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyScan(manualCode);
              }
            }}
            placeholder="Штрихкод или артикул…"
            autoComplete="off"
            inputMode="none"
          />
          <Button variant="subtle" onClick={() => setScannerOpen(true)}>
            <ScanLine size={15} /> Камера
          </Button>
          <Button variant="subtle" disabled={!manualCode.trim()} onClick={() => applyScan(manualCode)}>
            Проверить
          </Button>
        </div>
        <p className="text-[12px] text-mute">
          Позиции со штрихкодом — только сканом. Без штрихкода — вручную, уйдёт задача на проверку.
        </p>
      </div>

      {error && <div className="text-[12px] text-amber-300/90">{error}</div>}
      {notice && <div className="text-[12px] text-emerald-300/90">{notice}</div>}

      <ul className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
        {original.map((item, index) => {
          const key = keyOf(item, index);
          const ok = selectedKeys.has(key);
          const hasBarcode = !!item.barcode?.trim();
          return (
            <li
              key={key}
              className={`flex items-start gap-3 px-3 py-2.5 ${
                ok ? "bg-emerald-500/10" : "bg-ink-900/40"
              }`}
            >
              <span className="text-mute text-[12px] tabular-nums w-5 shrink-0 pt-0.5">
                {index + 1}.
              </span>
              <button
                type="button"
                className="flex-1 min-w-0 text-left"
                onClick={() => (ok ? deselectAt(index) : tryManualPick(item, index))}
              >
                <span className="block text-[14px] text-white break-words">{item.name}</span>
                <span
                  className={`block text-[12px] mt-0.5 ${ok ? "text-emerald-300" : "text-mute"}`}
                >
                  {item.qty} шт. · {money(Math.abs(item.price) * item.qty)}
                  {ok
                    ? " · выбрано"
                    : hasBarcode
                      ? " · нужен скан"
                      : " · без штрихкода — вручную"}
                </span>
              </button>
              {ok ? (
                <Check size={18} className="text-emerald-300 shrink-0 mt-0.5" />
              ) : (
                <span className="text-mute text-[12px] shrink-0 mt-0.5">
                  {hasBarcode ? "скан" : "вручную"}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
