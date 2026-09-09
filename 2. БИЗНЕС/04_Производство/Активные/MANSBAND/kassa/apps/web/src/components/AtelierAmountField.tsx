import { useMemo, useState } from "react";
import { Calculator, ChevronDown, ChevronUp } from "lucide-react";
import { Field } from "./ui";
import { ATELIER_GARMENT_LABEL, ATELIER_SERVICES, type AtelierServiceOption } from "@kassa/shared";
import { money } from "../lib/format";

/**
 * Поле «Ателье, ₽» с калькулятором работ по прайсу (созвон 09.09,
 * `ATELIER_SERVICES` в `@kassa/shared`). Диапазонные расценки прайса взяты по
 * верхней границе — консультант отмечает работы, сумма подставляется в поле,
 * но остаётся редактируемой руками, если объём работ по факту другой.
 */
export function AtelierAmountField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState<Record<string, boolean>>({});

  const sum = useMemo(
    () => ATELIER_SERVICES.filter((s) => checked[s.id]).reduce((acc, s) => acc + s.priceRub, 0),
    [checked]
  );

  const grouped = useMemo(() => {
    const map = new Map<AtelierServiceOption["garment"], AtelierServiceOption[]>();
    for (const s of ATELIER_SERVICES) {
      const list = map.get(s.garment) ?? [];
      list.push(s);
      map.set(s.garment, list);
    }
    return [...map.entries()];
  }, []);

  function apply() {
    onChange(String(sum));
    setOpen(false);
  }

  return (
    <Field label="Ателье, ₽">
      <div className="flex gap-2">
        <input
          className="input"
          inputMode="numeric"
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
          placeholder="0"
        />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          title="Калькулятор работ ателье"
          className="inline-flex items-center gap-1 rounded-lg border border-ink-700 px-2 text-mute hover:text-white hover:border-gold/40 shrink-0"
        >
          <Calculator size={15} />
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>
      {open && (
        <div className="mt-2 rounded-lg border border-ink-700 p-3 space-y-2.5 bg-ink-900/60">
          {grouped.map(([garment, services]) => (
            <div key={garment}>
              <div className="text-[12px] font-semibold text-mute-soft mb-1">
                {ATELIER_GARMENT_LABEL[garment]}
              </div>
              <div className="space-y-1">
                {services.map((s) => (
                  <label
                    key={s.id}
                    className="flex items-center justify-between gap-2 text-[13px] text-white/90"
                  >
                    <span className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={!!checked[s.id]}
                        onChange={(e) =>
                          setChecked((prev) => ({ ...prev, [s.id]: e.target.checked }))
                        }
                      />
                      {s.label}
                    </span>
                    <span className="text-mute shrink-0">{money(s.priceRub)}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}
          <div className="flex items-center justify-between border-t border-ink-700 pt-2">
            <span className="text-[13px] text-white font-semibold">Итого: {money(sum)}</span>
            <button
              type="button"
              onClick={apply}
              className="text-[13px] font-semibold text-gold-soft hover:text-white"
            >
              Подставить в поле
            </button>
          </div>
        </div>
      )}
    </Field>
  );
}
