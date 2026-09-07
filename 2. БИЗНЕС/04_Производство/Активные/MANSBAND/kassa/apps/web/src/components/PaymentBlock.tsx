import { useMemo, useState } from "react";
import { Plus, Trash2, Banknote, CreditCard, Landmark, Gift } from "lucide-react";
import type { Payment } from "../data/types";
import { findPaymentMethod } from "../data/mock";
import { PaymentMethodSelect } from "./PaymentMethodSelect";
import { dateCompact, money } from "../lib/format";
import { useStore } from "../store";

function methodIcon(kind: string) {
  if (kind === "cash") return <Banknote size={15} />;
  if (kind === "card") return <CreditCard size={15} />;
  if (kind === "account") return <Landmark size={15} />;
  return <Gift size={15} />;
}

function todayYmd(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Сколько уже списано с сертификата в текущей форме (ещё не сохранено). */
function spentOnCertInForm(payments: Payment[], certNumber: string): number {
  const n = certNumber.trim();
  if (!n) return 0;
  return payments
    .filter((p) => p.certificateNumber?.trim() === n)
    .reduce((s, p) => s + p.amount, 0);
}

/** Строки оплат — один вид и в форме продажи, и в карточке заявки. */
export function PaymentLines({
  payments,
  onRemove,
  certRemainders,
}: {
  payments: Payment[];
  onRemove?: (id: string) => void;
  /** Остаток по сертификату после этой строки оплаты (по номеру). */
  certRemainders?: Record<string, number>;
}) {
  if (payments.length === 0) return null;
  return (
    <div className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
      {payments.map((p) => {
        const m = findPaymentMethod(p.methodId);
        const when = p.paidAt ? dateCompact(p.paidAt) : null;
        const certNo = p.certificateNumber?.trim();
        const rem = certNo && certRemainders ? certRemainders[p.id] : undefined;
        return (
          <div key={p.id} className="flex items-center gap-3 px-3 py-2.5 bg-ink-900/50">
            <span className="text-gold shrink-0">{methodIcon(m?.kind ?? "card")}</span>
            <div className="flex-1 min-w-0">
              <div className="text-[14px] text-white truncate">
                {m?.label ?? p.methodId}
                {certNo ? ` №${certNo}` : ""}
              </div>
              <div className="text-[11px] text-mute mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
                {when && <span>{when}</span>}
                {typeof rem === "number" && (
                  <span className="text-emerald-300/90">
                    остаток по серт. {money(rem)}
                  </span>
                )}
              </div>
            </div>
            <span className="font-semibold text-white shrink-0">{money(p.amount)}</span>
            {onRemove && (
              <button onClick={() => onRemove(p.id)} className="text-mute hover:text-white shrink-0" type="button">
                <Trash2 size={15} />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
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
  const [methodId, setMethodId] = useState("");
  const [amount, setAmount] = useState("");
  const [certNo, setCertNo] = useState("");
  const [certError, setCertError] = useState<string | null>(null);

  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const remainder = total - paid;

  const selected = methodId ? findPaymentMethod(methodId) : undefined;
  const isCert = selected?.kind === "certificate";

  const cert = isCert && certNo.trim()
    ? certificates.find((c) => c.number === certNo.trim())
    : undefined;

  const spentInForm = cert ? spentOnCertInForm(payments, cert.number) : 0;
  const available = cert ? Math.max(0, cert.balance - spentInForm) : 0;

  const amountNum = Number(amount) || 0;
  const chargePreview = cert ? Math.min(Math.max(0, amountNum), available) : 0;
  const remainAfterPreview = cert ? Math.max(0, available - chargePreview) : 0;

  const fillValue = isCert && cert ? Math.min(Math.max(0, remainder), available) : Math.max(0, remainder);
  // Сколько станет сдачей, если добавить введённую сумму (для не-серта — полная сумма строки).
  const changeIfAdded = !isCert && amountNum > 0
    ? Math.max(0, paid + amountNum - total)
    : !isCert
      ? 0
      : Math.max(0, paid + chargePreview - total);

  /** Для каждой строки оплаты сертификатом — остаток после неё (нарастающим итогом). */
  const certRemainders = useMemo(() => {
    const running: Record<string, number> = {};
    const out: Record<string, number> = {};
    for (const p of payments) {
      const n = p.certificateNumber?.trim();
      if (!n) continue;
      if (running[n] == null) {
        const c = certificates.find((x) => x.number === n);
        running[n] = c?.balance ?? 0;
      }
      running[n] = Math.max(0, running[n]! - p.amount);
      out[p.id] = running[n]!;
    }
    return out;
  }, [payments, certificates]);

  function add() {
    let a = Number(amount);
    if (!methodId || !a || a <= 0) return;
    const m = findPaymentMethod(methodId);
    if (!m) return;
    if (m.kind === "certificate") {
      if (!certNo.trim()) {
        setCertError("Укажите номер сертификата");
        return;
      }
      if (!cert) {
        setCertError("Сертификат не найден в системе — оплату провести нельзя");
        return;
      }
      if (available <= 0) {
        setCertError("У сертификата нулевой доступный баланс");
        return;
      }
      a = Math.min(a, available);
      setCertError(null);
    }
    onChange([
      ...payments,
      {
        id: crypto.randomUUID(),
        methodId,
        amount: a,
        certificateNumber: m.kind === "certificate" ? certNo.trim() : undefined,
        paidAt: todayYmd(),
      },
    ]);
    setAmount("");
    // Номер серт. оставляем — удобно видеть остаток и добить ещё списанием
  }

  const methodMissing = !methodId;
  const certBlocked = Boolean(isCert && (!certNo.trim() || !cert || available <= 0));
  const addBlocked = methodMissing || certBlocked;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[180px]">
          <div className="field-label">Способ оплаты</div>
          <PaymentMethodSelect
            value={methodId}
            placeholder="— выбрать —"
            onChange={(next) => {
              setMethodId(next);
              setCertError(null);
            }}
          />
        </div>
        <div className="w-[150px]">
          <div className="field-label flex items-center justify-between gap-2">
            <span>Сумма</span>
            {fillValue > 0 && !addBlocked && (
              <button
                type="button"
                onClick={() => setAmount(String(Math.round(fillValue)))}
                className="text-[11px] text-gold-soft hover:text-white normal-case tracking-normal font-semibold whitespace-nowrap"
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
            onKeyDown={(e) => e.key === "Enter" && !addBlocked && add()}
          />
        </div>
        <button
          onClick={add}
          disabled={addBlocked}
          title={
            methodMissing
              ? "Выберите способ оплаты"
              : certBlocked
                ? "Сначала укажите найденный в системе сертификат"
                : undefined
          }
          className={`inline-flex items-center gap-1.5 rounded-lg font-semibold px-3 py-2.5 ${
            addBlocked
              ? "bg-ink-800 text-mute cursor-not-allowed"
              : "bg-ink-700 hover:bg-ink-600 text-white"
          }`}
        >
          <Plus size={16} /> Добавить
        </button>
      </div>

      {isCert && (
        <div className="rounded-lg border border-ink-700 bg-ink-900/40 p-3 space-y-3">
          <div className="grid sm:grid-cols-[minmax(140px,180px)_1fr] gap-3 items-end">
            <div>
              <div className="field-label">№ сертификата</div>
              <input
                className={`input ${certNo.trim() && !cert ? "border-red-400/60" : ""}`}
                placeholder="2420"
                value={certNo}
                onChange={(e) => {
                  setCertNo(e.target.value);
                  setCertError(null);
                }}
              />
            </div>
            <div className="min-w-0">
              {!certNo.trim() ? (
                <p className="text-[13px] text-mute">Введите номер — покажем баланс и остаток после списания</p>
              ) : !cert ? (
                <p className="text-[13px] text-red-300">Сертификат не найден в системе</p>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[13px]">
                  <div className="rounded-md bg-ink-800/70 px-2.5 py-2">
                    <div className="text-[11px] text-mute uppercase tracking-wide">На серт.</div>
                    <div className="text-white font-semibold whitespace-nowrap">{money(cert.balance)}</div>
                    <div className="text-[11px] text-mute whitespace-nowrap">из {money(cert.nominal)}</div>
                  </div>
                  <div className="rounded-md bg-ink-800/70 px-2.5 py-2">
                    <div className="text-[11px] text-mute uppercase tracking-wide">Доступно</div>
                    <div className="text-emerald-300 font-semibold whitespace-nowrap">{money(available)}</div>
                    {spentInForm > 0 && (
                      <div className="text-[11px] text-mute whitespace-nowrap">−{money(spentInForm)} в чеке</div>
                    )}
                  </div>
                  <div className="rounded-md bg-ink-800/70 px-2.5 py-2 col-span-2 sm:col-span-1">
                    <div className="text-[11px] text-mute uppercase tracking-wide">Останется</div>
                    <div
                      className={`font-semibold whitespace-nowrap ${
                        amountNum > 0 ? "text-gold-soft" : "text-white"
                      }`}
                    >
                      {money(amountNum > 0 ? remainAfterPreview : available)}
                    </div>
                    {amountNum > 0 && chargePreview > 0 && (
                      <div className="text-[11px] text-mute whitespace-nowrap">
                        после −{money(chargePreview)}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {certError && (
        <div className="text-[13px] text-red-300 bg-red-400/10 border border-red-400/30 rounded-lg px-3 py-2">
          {certError}
        </div>
      )}

      {changeIfAdded > 0 && (
        <div className="text-[13px] text-amber-100/95 bg-amber-400/10 border border-amber-400/35 rounded-lg px-3 py-2">
          Сдача {money(changeIfAdded)} — после «Добавить» ниже появятся чаевые и как выдать сдачу клиенту.
        </div>
      )}

      <PaymentLines
        payments={payments}
        certRemainders={certRemainders}
        onRemove={(id) => onChange(payments.filter((x) => x.id !== id))}
      />
    </div>
  );
}
