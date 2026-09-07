import { Plus, Trash2 } from "lucide-react";
import {
  MANSBAND_PAYOUT_METHOD,
  type Payout,
} from "@kassa/shared";
import { money } from "../lib/format";
import { PaymentMethodSelect } from "./PaymentMethodSelect";

/**
 * Раскладка выплаты по способам: часть наличными, часть переводом, часть —
 * строкой «Переведёт Mansband» (уходит в очередь Эдвина).
 *
 * Любая строка редактируется: при правке суммы остаток уходит в «парную»
 * строку (для двух строк — в соседнюю; для большего числа — в последнюю,
 * а если правят последнюю — в предпоследнюю). Сумма строк всегда = total.
 */
export function PayoutRows({
  total,
  rows,
  onChange,
  label,
  destination,
  onDestination,
  destinationLabel = "Куда переводить",
  destinationHint = "Номер телефона или карты · Банк",
  destinationReadOnly = false,
}: {
  total: number;
  rows: Payout[] | undefined;
  onChange: (rows: Payout[]) => void;
  label: string;
  /** @deprecated подпись «можно частями» убрана из UI */
  hint?: string;
  destination?: string;
  onDestination?: (value: string) => void;
  destinationLabel?: string;
  destinationHint?: string;
  destinationReadOnly?: boolean;
}) {
  if (total <= 0) return null;
  const target = Math.max(0, Math.round(total));
  const lines = ensureLines(rows, target);
  const hasMansband = lines.some((row) => row.methodId === MANSBAND_PAYOUT_METHOD && row.amount > 0);

  function updateMethod(index: number, methodId: string) {
    onChange(lines.map((row, i) => (i === index ? { ...row, methodId } : row)));
  }

  function updateAmount(index: number, raw: number) {
    const amount = Math.max(0, Math.min(Math.round(raw) || 0, target));
    if (lines.length === 1) {
      onChange([{ ...lines[0]!, amount: target }]);
      return;
    }
    // Парная строка принимает остаток: при правке последней — предпоследняя,
    // иначе последняя. Так редактируется и первое, и второе поле.
    const flex = index === lines.length - 1 ? lines.length - 2 : lines.length - 1;
    const next = lines.map((row) => ({ ...row }));
    next[index] = { ...next[index]!, amount };

    let locked = 0;
    for (let i = 0; i < next.length; i++) {
      if (i === flex) continue;
      locked += next[i]!.amount;
    }
    if (locked > target) {
      // Другие зафиксированные строки не влезают — урезаем текущую.
      const othersLocked = locked - amount;
      const capped = Math.max(0, target - othersLocked);
      next[index] = { ...next[index]!, amount: capped };
      locked = othersLocked + capped;
    }
    next[flex] = { ...next[flex]!, amount: Math.max(0, target - locked) };
    onChange(next);
  }

  function addRow() {
    const base = [...lines, { methodId: "", amount: 0 }];
    // Новая строка получает 0, остаток остаётся на прежней последней через
    // пересчёт: правим новую (index = last) → flex = предпоследняя.
    onChange(rebalanceKeeping(base, target, base.length - 1));
  }

  function removeRow(index: number) {
    if (lines.length <= 1) return;
    const next = lines.filter((_, i) => i !== index);
    onChange(rebalanceKeeping(next, target, Math.min(index, next.length - 1)));
  }

  return (
    <div className="space-y-2.5">
      {label.trim() ? (
        <div className="field-label">
          {label} · {money(target)}
        </div>
      ) : null}
      <div className="space-y-2">
        {lines.map((row, index) => (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <div className="flex-1 min-w-[170px]">
              <PaymentMethodSelect
                value={row.methodId}
                withCertificate={false}
                withMansband
                placeholder="— выбрать —"
                onChange={(methodId) => updateMethod(index, methodId)}
              />
            </div>
            <input
              className="input w-[130px] text-right tabular-nums"
              inputMode="numeric"
              value={row.amount || ""}
              placeholder="0"
              onChange={(e) => updateAmount(index, Number(e.target.value.replace(/[^\d]/g, "")) || 0)}
            />
            {lines.length > 1 && (
              <button
                type="button"
                onClick={() => removeRow(index)}
                className="text-mute hover:text-white"
                title="Убрать строку"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={addRow}
        className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-gold-soft hover:text-white"
      >
        <Plus size={14} /> Ещё способ
      </button>
      {hasMansband && onDestination && (
        <label className="block">
          <div className="field-label">{destinationLabel}</div>
          <input
            className="input"
            value={destination ?? ""}
            readOnly={destinationReadOnly}
            placeholder="+7 9XX или 2202 2004 XXXX · Сбербанк"
            onChange={(e) => onDestination(e.target.value)}
          />
          {!destinationReadOnly && !(destination ?? "").trim() && (
            <div className="mt-1 text-[11px] text-amber-300/80">{destinationHint}</div>
          )}
        </label>
      )}
    </div>
  );
}

/** Одна строка на всю сумму, если раскладки ещё нет. Способ — пустой («— выбрать —»). */
function ensureLines(rows: Payout[] | undefined, total: number): Payout[] {
  if (!rows || rows.length === 0) {
    return [{ methodId: "", amount: total }];
  }
  const out = rows.map((row) => ({
    methodId: row.methodId ?? "",
    amount: Math.max(0, Math.round(row.amount) || 0),
  }));
  const sum = out.reduce((s, row) => s + row.amount, 0);
  if (sum === total) return out;
  // Подтянуть сумму к total, не ломая уже введённые строки: правим последнюю.
  return rebalanceKeeping(out, total, out.length - 1);
}

/**
 * Выставляет amount у focusIdx так, чтобы сумма стала total
 * (остальные строки не трогаем, кроме урезания focus при переборе).
 */
function rebalanceKeeping(rows: Payout[], total: number, focusIdx: number): Payout[] {
  const next = rows.map((row) => ({ ...row }));
  if (next.length === 0) return [{ methodId: "", amount: total }];
  const idx = Math.max(0, Math.min(focusIdx, next.length - 1));
  let others = 0;
  for (let i = 0; i < next.length; i++) {
    if (i === idx) continue;
    others += next[i]!.amount;
  }
  next[idx] = { ...next[idx]!, amount: Math.max(0, total - others) };
  return next;
}
