import { useState } from "react";
import { Plus, Trash2, Banknote, CreditCard, Landmark, Gift } from "lucide-react";
import type { Payment } from "../data/types";
import { PAYMENT_METHODS } from "../data/mock";
import { money } from "../lib/format";
import { useStore } from "../store";
import { Badge } from "./ui";

function methodIcon(kind: string) {
  if (kind === "cash") return <Banknote size={15} />;
  if (kind === "card") return <CreditCard size={15} />;
  if (kind === "account") return <Landmark size={15} />;
  return <Gift size={15} />;
}

export function PaymentBlock({
  total,
  payments,
  onChange,
}: {
  total: number;
  payments: Payment[];
  onChange: (p: Payment[]) => void;
}) {
  const { certificates } = useStore();
  const [methodId, setMethodId] = useState(PAYMENT_METHODS[0].id);
  const [amount, setAmount] = useState("");
  const [certNo, setCertNo] = useState("");

  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const remainder = total - paid;
  const change = paid > total ? paid - total : 0;

  const selected = PAYMENT_METHODS.find((x) => x.id === methodId)!;

  // Сертификат как способ оплаты: ищем баланс по номеру
  const cert =
    selected.kind === "certificate" && certNo
      ? certificates.find((c) => c.number === certNo.trim())
      : undefined;

  // Сколько можно подставить по кнопке «остаток»: для сертификата — не больше его баланса
  const fillValue =
    selected.kind === "certificate" && cert
      ? Math.min(Math.max(0, remainder), cert.balance)
      : Math.max(0, remainder);

  function add() {
    let a = Number(amount);
    if (!a || a <= 0) return;
    const m = PAYMENT_METHODS.find((x) => x.id === methodId)!;
    // Сертификатом нельзя списать больше его баланса
    if (m.kind === "certificate" && cert) {
      a = Math.min(a, cert.balance);
    }
    onChange([
      ...payments,
      {
        id: crypto.randomUUID(),
        methodId,
        amount: a,
        certificateNumber: m.kind === "certificate" ? certNo : undefined,
      },
    ]);
    setAmount("");
    setCertNo("");
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[180px]">
          <div className="field-label">Способ оплаты</div>
          <select
            className="input"
            value={methodId}
            onChange={(e) => setMethodId(e.target.value)}
          >
            {PAYMENT_METHODS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        {selected.kind === "certificate" && (
          <div className="w-[160px]">
            <div className="field-label">№ серт.</div>
            <input
              className="input"
              placeholder="45578"
              value={certNo}
              onChange={(e) => setCertNo(e.target.value)}
            />
            {certNo.trim() && (
              cert ? (
                <div className="mt-1 text-[12px] text-emerald-300/90">
                  Баланс: {money(cert.balance)} из {money(cert.nominal)}
                </div>
              ) : (
                <div className="mt-1 text-[12px] text-amber-300/80">Сертификат не найден</div>
              )
            )}
          </div>
        )}
        <div className="w-[150px]">
          <div className="field-label flex items-center justify-between">
            <span>Сумма</span>
            {fillValue > 0 && (
              <button
                type="button"
                onClick={() => setAmount(String(Math.round(fillValue)))}
                className="text-[11px] text-gold-soft hover:text-white normal-case tracking-normal font-semibold"
                title="Подставить сумму остатка"
              >
                внести остаток
              </button>
            )}
          </div>
          <input
            className="input"
            inputMode="numeric"
            placeholder="0"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
        </div>
        <button
          onClick={add}
          className="inline-flex items-center gap-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-white font-semibold px-3 py-2.5"
        >
          <Plus size={16} /> Добавить
        </button>
      </div>

      {payments.length > 0 && (
        <div className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
          {payments.map((p) => {
            const m = PAYMENT_METHODS.find((x) => x.id === p.methodId)!;
            return (
              <div key={p.id} className="flex items-center gap-3 px-3 py-2.5 bg-ink-900/50">
                <span className="text-gold">{methodIcon(m.kind)}</span>
                <span className="flex-1 text-[14px] text-white">
                  {m.label}
                  {p.certificateNumber ? ` №${p.certificateNumber}` : ""}
                </span>
                <span className="font-semibold text-white">{money(p.amount)}</span>
                <button
                  onClick={() => onChange(payments.filter((x) => x.id !== p.id))}
                  className="text-mute hover:text-white"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-ink-900 border border-ink-700 py-2.5">
          <div className="field-label mb-0">К оплате</div>
          <div className="font-bold text-white">{money(total)}</div>
        </div>
        <div className="rounded-lg bg-ink-900 border border-ink-700 py-2.5">
          <div className="field-label mb-0">Оплачено</div>
          <div className="font-bold text-white">{money(paid)}</div>
        </div>
        {change > 0 ? (
          <div className="rounded-lg bg-amber-400/15 border-2 border-amber-400/60 py-2.5 relative overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-amber-400/5 to-transparent pointer-events-none" />
            <div className="text-[11px] uppercase tracking-wider font-bold text-amber-300/90 mb-0.5">Сдача</div>
            <div className="font-extrabold text-amber-200 text-[18px] leading-tight">{money(change)}</div>
          </div>
        ) : (
          <div className="rounded-lg bg-ink-900 border border-ink-700 py-2.5">
            <div className="field-label mb-0">Остаток</div>
            <div className="font-bold text-white">
              {money(Math.max(0, remainder))}
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        {remainder <= 0 ? (
          <Badge tone="green">Оплачен</Badge>
        ) : paid > 0 ? (
          <Badge tone="amber">Частично оплачен</Badge>
        ) : (
          <Badge tone="red">Не оплачен</Badge>
        )}
        {change > 0 && <Badge tone="amber">Сдача {money(change)}</Badge>}
      </div>
    </div>
  );
}
