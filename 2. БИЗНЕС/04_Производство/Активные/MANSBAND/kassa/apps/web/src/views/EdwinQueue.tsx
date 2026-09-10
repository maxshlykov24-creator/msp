import { useEffect, useMemo, useState } from "react";
import { Send, Check, ArrowLeftRight, Banknote, Clock3, Coins, ReceiptText, Plus, Trash2, Undo2, Wallet } from "lucide-react";
import { useStore } from "../store";
import { money, timeOf, shortDate } from "../lib/format";
import { Button, Modal, StatTile } from "../components/ui";
import { api, USE_MOCK } from "../api/client";
import { EDWIN_CASH_METHOD, EDWIN_EXPENSE_CATEGORIES, paymentMethodLabel } from "@kassa/shared";
import { DEFAULT_PAYOUT_METHOD_ID, PaymentMethodSelect } from "../components/PaymentMethodSelect";

const EXPENSE_METHOD_PLACEHOLDER = "— выбрать —";
const EXPENSE_EXTRA_METHODS = [EDWIN_CASH_METHOD];
import { useAuth } from "../auth/AuthContext";
import { canSeeFinanceQueues } from "../auth/roles";
import type { Deal } from "../data/types";
import { DealWorkspace } from "./DealWorkspace";
import { Hint } from "../lib/hints";

type ExtendedQueueItem = Omit<ReturnType<typeof useStore>["queue"][number], "kind"> & {
  kind:
    | "change"
    | "refund"
    | "tips"
    | "invoice"
    | "invoice_check"
    | "documents"
    | "documents_hand"
    | "atelier"
    | "salary";
  issueMethod?: string;
};

/** Строка ведомости ЗП в metadata позиции очереди (см. api/services/payroll.ts). */
interface SalaryRow {
  consultant: string;
  basePay: number;
  /** Выручка за период — база премий и штрафов в процентах (созвон 04.09). */
  periodRevenue?: number;
  conversionPct: number | null;
  conversionBonusPct?: number;
  conversionBonus: number;
  upt: number | null;
  uptBonusPct?: number;
  uptBonus: number;
  conversionPenaltyPct?: number;
  conversionPenalty?: number;
  uptPenaltyPct?: number;
  uptPenalty?: number;
  total: number;
  days: Array<{ date: string; revenue: number; pctAmount: number; payout: number; floorApplied: boolean }>;
}

/** Подпись премии/штрафа: «5% от 120 000 ₽» — видно, откуда сумма. */
function pctHint(pct: number | undefined, revenue: number | undefined): string {
  if (!pct) return "";
  return revenue != null ? ` · ${pct}% от ${money(revenue)}` : ` · ${pct}%`;
}

const KIND_LABEL: Record<string, string> = {
  change: "Сдача",
  refund: "Сделать возврат",
  tips: "Чаевые",
  invoice: "Выставить счет",
  invoice_check: "Проверить оплату",
  documents: "Подготовить документы",
  documents_hand: "Передать документы",
  atelier: "Оплатить ателье",
  salary: "Выдать зарплату",
};

/** Счёт компании: номер и дата — не выдача наличных. */
function isCompanyInvoiceTask(kind: string): boolean {
  return kind === "invoice" || kind === "invoice_check";
}

/** Позиции очереди Миши в очереди Эдвина не показываем — у них свой экран. */
const MISHA_KINDS = new Set(["documents", "documents_hand", "atelier"]);

function todayYmd(): string {
  return new Date().toISOString().slice(0, 10);
}

interface ExpenseSplitRow {
  id: string;
  methodId: string;
  amount: string;
}

export function EdwinQueue() {
  const { queue, issueQueueItem, reopenQueueItem, deals } = useStore();
  const { user } = useAuth();
  const allowed = canSeeFinanceQueues(user?.role, user?.login, user?.name);
  const rows = queue as unknown as ExtendedQueueItem[];
  const [kind, setKind] = useState("all");
  const [status, setStatus] = useState("pending");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [reopening, setReopening] = useState<string | null>(null);
  const [issuing, setIssuing] = useState<ExtendedQueueItem | null>(null);
  const [issueMethod, setIssueMethod] = useState(DEFAULT_PAYOUT_METHOD_ID);
  const [issueAmount, setIssueAmount] = useState("");
  const [balances, setBalances] = useState<Array<{ account: string; balance: number }>>([]);
  const [invoiceItem, setInvoiceItem] = useState<ExtendedQueueItem | null>(null);
  const [invoiceNo, setInvoiceNo] = useState("");
  const [invoiceDate, setInvoiceDate] = useState(todayYmd());
  const [invoiceError, setInvoiceError] = useState<string | null>(null);
  const [salaryItem, setSalaryItem] = useState<ExtendedQueueItem | null>(null);
  const [salaryExpanded, setSalaryExpanded] = useState<string | null>(null);
  const [openDeal, setOpenDeal] = useState<Deal | null>(null);
  const [dealLoading, setDealLoading] = useState(false);
  const [dealError, setDealError] = useState<string | null>(null);

  const [expenseOpen, setExpenseOpen] = useState(false);
  const [expenseCategory, setExpenseCategory] = useState<string>(EDWIN_EXPENSE_CATEGORIES[0]);
  const [expenseAmount, setExpenseAmount] = useState("");
  const [expenseMethod, setExpenseMethod] = useState("");
  const [expenseComment, setExpenseComment] = useState("");
  const [expenseDate, setExpenseDate] = useState(todayYmd());
  const [expenseSplits, setExpenseSplits] = useState<ExpenseSplitRow[]>([]);
  const [expenseError, setExpenseError] = useState<string | null>(null);

  const edwinRows = rows.filter((q) => !MISHA_KINDS.has(q.kind));
  const pending = edwinRows.filter((q) => q.status === "pending");
  const issued = edwinRows.filter((q) => q.status === "issued");
  const pendingSum = pending.reduce((s, q) => s + (q.amount - (q.issuedAmount ?? 0)), 0);
  const filtered = useMemo(() => rows.filter((q) => {
    const day = q.createdAt.slice(0, 10);
    if (MISHA_KINDS.has(q.kind)) return false;
    return (kind === "all" || q.kind === kind) &&
      (status === "all" || q.status === status) &&
      (!dateFrom || day >= dateFrom) &&
      (!dateTo || day <= dateTo);
  }), [rows, kind, status, dateFrom, dateTo]);

  function loadBalances() {
    if (USE_MOCK || !allowed) return;
    api.get<Array<{ account: string; balance: number }>>("/balances").then(setBalances).catch(() => {});
  }

  useEffect(() => {
    loadBalances();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed]);

  const issuingRemainder = issuing ? issuing.amount - (issuing.issuedAmount ?? 0) : 0;

  async function openDealByNumber(dealNumber: number) {
    setDealError(null);
    setOpenDeal(null);
    const cached = deals.find((d) => d.number === dealNumber);
    if (cached) {
      setOpenDeal(cached);
      return;
    }
    if (USE_MOCK) {
      setDealError(`Заявка #${dealNumber} не найдена в списке`);
      return;
    }
    setDealLoading(true);
    try {
      const deal = await api.get<Deal | null>(`/deals/${dealNumber}`);
      if (!deal || typeof deal !== "object" || !("number" in deal)) {
        setDealError(`Заявка #${dealNumber} не найдена`);
        return;
      }
      setOpenDeal(deal);
    } catch (err) {
      setDealError(err instanceof Error ? err.message : `Заявка #${dealNumber} не найдена`);
    } finally {
      setDealLoading(false);
    }
  }

  function openIssue(item: ExtendedQueueItem) {
    setIssuing(item);
    setIssueMethod(DEFAULT_PAYOUT_METHOD_ID);
    setIssueAmount(String(Math.round(item.amount - (item.issuedAmount ?? 0))));
  }

  function openInvoice(item: ExtendedQueueItem) {
    setInvoiceItem(item);
    const metaNo = item.metadata?.invoiceNo as string | undefined;
    setInvoiceNo(String(metaNo || item.destination || ""));
    setInvoiceDate(String((item.metadata?.invoiceDate as string | undefined) || todayYmd()));
    setInvoiceError(null);
  }

  /**
   * «Счёт выставлен» → сразу задача «Проверить оплату»;
   * «Счёт оплачен» → цепочка Миши.
   */
  async function saveInvoice(markPaid: boolean) {
    if (!invoiceItem) return;
    setInvoiceError(null);
    if (!USE_MOCK) {
      try {
        await api.patch(`/deals/${invoiceItem.dealNumber}/company`, {
          invoiceNo: invoiceNo.trim(),
          invoiceDate,
          invoiceStatus: markPaid ? "paid" : "awaiting_payment",
        });
      } catch (err) {
        setInvoiceError(err instanceof Error ? err.message : "Не удалось обновить счёт");
        return;
      }
    }
    setInvoiceItem(null);
  }

  function companyMeta(q: ExtendedQueueItem) {
    const m = q.metadata ?? {};
    const invoiceNoMeta =
      (typeof m.invoiceNo === "string" && m.invoiceNo.trim()) ||
      (q.kind === "invoice_check" ? String(q.destination || "").trim() : "");
    const company =
      (typeof m.companyName === "string" && m.companyName.trim()) ||
      q.client ||
      "";
    const buyer =
      typeof m.buyerName === "string" && m.buyerName.trim() ? m.buyerName.trim() : "";
    return { invoiceNoMeta, company, buyer };
  }

  function confirmIssue() {
    if (!issuing) return;
    const requested = Number(issueAmount) || issuingRemainder;
    const amount = Math.min(Math.max(1, requested), issuingRemainder);
    issueQueueItem(issuing.id, issueMethod, amount);
    setIssuing(null);
    window.setTimeout(loadBalances, 300);
  }

  async function reopenItem(item: ExtendedQueueItem) {
    if (!confirm(`Вернуть «${KIND_LABEL[item.kind] ?? item.kind}» #${item.dealNumber} в работу?`)) {
      return;
    }
    setReopening(item.id);
    try {
      await reopenQueueItem(item.id);
      window.setTimeout(loadBalances, 300);
    } catch {
      // store уже откатил через reloadQueue
    } finally {
      setReopening(null);
    }
  }

  // Разбивка расхода: пока строк нет — весь расход идёт одним способом.
  const splitsTotal = expenseSplits.reduce((s, row) => s + (Number(row.amount) || 0), 0);

  function addSplit() {
    setExpenseSplits((prev) => {
      // Первый клик: сразу два способа — текущий «Способ оплаты» + вторая строка.
      // Иначе пришлось бы жать кнопку дважды, чтобы разбить оплату.
      if (prev.length === 0) {
        return [
          { id: crypto.randomUUID(), methodId: expenseMethod, amount: "" },
          { id: crypto.randomUUID(), methodId: "", amount: "" },
        ];
      }
      const currentTotal = prev.reduce((s, row) => s + (Number(row.amount) || 0), 0);
      const rest = Math.max(0, (Number(expenseAmount) || 0) - currentTotal);
      return [
        ...prev,
        { id: crypto.randomUUID(), methodId: "", amount: rest ? String(rest) : "" },
      ];
    });
  }

  function resetExpense() {
    setExpenseAmount("");
    setExpenseMethod("");
    setExpenseComment("");
    setExpenseSplits([]);
    setExpenseDate(todayYmd());
    setExpenseError(null);
  }

  async function saveExpense() {
    const amount = Number(expenseAmount);
    if (!amount) return;
    if (expenseSplits.length === 0 && !expenseMethod) {
      setExpenseError("Выберите способ оплаты");
      return;
    }
    if (expenseSplits.some((row) => !row.methodId)) {
      setExpenseError("Выберите способ оплаты во всех строках");
      return;
    }
    if (expenseSplits.length > 0 && Math.abs(splitsTotal - amount) > 0.01) {
      setExpenseError("Разбивка по способам не сходится с суммой расхода");
      return;
    }
    setExpenseError(null);
    if (!USE_MOCK) {
      try {
        await api.post("/expenses", {
          category: expenseCategory,
          amount,
          account: expenseSplits[0]?.methodId ?? expenseMethod,
          splits: expenseSplits.length
            ? expenseSplits.map((row) => ({ methodId: row.methodId, amount: Number(row.amount) || 0 }))
            : undefined,
          description: expenseComment.trim() || undefined,
          spentAt: new Date(`${expenseDate}T12:00:00`).toISOString(),
        });
      } catch {
        setExpenseError("Не удалось сохранить расход — попробуйте ещё раз");
        return;
      }
    }
    setExpenseOpen(false);
    resetExpense();
    loadBalances();
  }

  if (!allowed) {
    return (
      <div className="max-w-2xl mx-auto card py-10 text-center text-mute">
        Очередь Эдвина доступна только финансам, РОПу и администратору. Сдачу и чаевые, которые
        выдали сами, отмечайте в карточке заявки.
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Send className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Очередь Эдвина</h1>
      </div>
      <Hint>
        Сдача, чаевые, возвраты, счета и выдача зарплаты. Для утренней сверки фиксируются кто,
        когда и каким способом выдал деньги. Расход пишется на выбранный счёт.
      </Hint>

      <div className="grid sm:grid-cols-3 gap-3 mb-5">
        <StatTile label="К выдаче сейчас" value={String(pending.length)} tone="amber" sub={money(pendingSum)} />
        <StatTile label="Выдано сегодня" value={String(issued.length)} tone="green" />
        <button className="card p-4 text-left hover:border-gold/40" onClick={() => setExpenseOpen(true)}>
          <div className="field-label">Расход</div>
          <div className="text-white font-bold flex items-center gap-2"><Plus size={16} /> Внести расход</div>
        </button>
      </div>
      {balances.length > 0 && (
        <div className="card p-3 mb-4">
          <div className="field-label mb-2">Расходы по счетам</div>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            {balances.map((row) => (
              <span key={row.account} className="text-mute">
                {paymentMethodLabel(row.account)}: <strong className="text-white">{money(row.balance)}</strong>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card p-3 mb-4 grid sm:grid-cols-4 gap-2">
        <select className="input py-2 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">Все виды</option>
          <option value="change">Сдача</option>
          <option value="tips">Чаевые</option>
          <option value="refund">Возвраты</option>
          <option value="invoice">Выставить счет</option>
          <option value="invoice_check">Проверить оплату</option>
          <option value="salary">Зарплата</option>
        </select>
        <select className="input py-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">Нужно выдать</option><option value="issued">Выдано</option><option value="all">Все</option>
        </select>
        <input type="date" className="input py-2 text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label="Период с" />
        <input type="date" className="input py-2 text-sm" value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} aria-label="Период по" />
      </div>

      <div className="field-label mb-2">{status === "issued" ? "Выдано" : status === "pending" ? "К выдаче" : "Все операции"}</div>
      <div className="space-y-2.5">
        {filtered.length === 0 && <div className="card text-center text-mute py-8">По фильтру операций нет</div>}
        {filtered.map((q) => {
          const already = q.issuedAmount ?? 0;
          const remainder = Math.max(0, q.amount - already);
          return (
            <div key={q.id} className="card p-0 overflow-hidden flex">
              <div className={`w-1.5 ${q.kind === "refund" ? "bg-white/70" : "bg-white/35"}`} />
              <div className="flex-1 p-4 flex flex-wrap items-center gap-3">
                <div className="w-10 h-10 rounded-xl grid place-items-center bg-white/10 text-white">
                  {q.kind === "salary" ? (
                    <Wallet size={18} />
                  ) : q.kind === "refund" ? (
                    <ArrowLeftRight size={18} />
                  ) : q.kind === "tips" ? (
                    <Coins size={18} />
                  ) : isCompanyInvoiceTask(q.kind) ? (
                    <ReceiptText size={18} />
                  ) : (
                    <Banknote size={18} />
                  )}
                </div>
                <button
                  className="flex-1 min-w-[180px] text-left"
                  onClick={() => {
                    if (q.kind === "salary") {
                      setSalaryItem(q);
                      setSalaryExpanded(null);
                    } else {
                      void openDealByNumber(q.dealNumber);
                    }
                  }}
                >
                  {q.kind === "salary" ? (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-white font-semibold">{money(q.amount)}</span>
                        <span className="chip bg-ink-700 text-mute">Выдать зарплату</span>
                      </div>
                      <div className="text-mute text-[13px] truncate">
                        {q.destination} · ведомость по консультантам
                      </div>
                    </>
                  ) : isCompanyInvoiceTask(q.kind) ? (
                    (() => {
                      const { invoiceNoMeta, company, buyer } = companyMeta(q);
                      const title =
                        q.kind === "invoice_check"
                          ? `Проверить оплату · #${q.dealNumber}`
                          : `Выставить счет · #${q.dealNumber}`;
                      const metaLine = [
                        company || null,
                        buyer && buyer !== company ? buyer : null,
                        invoiceNoMeta ? `счёт ${invoiceNoMeta}` : null,
                        q.amount > 0 ? money(q.amount) : null,
                      ]
                        .filter(Boolean)
                        .join(" · ");
                      return (
                        <>
                          <div className="text-white font-semibold text-[14px]">{title}</div>
                          {metaLine && (
                            <div className="text-mute text-[13px] mt-0.5 truncate">{metaLine}</div>
                          )}
                        </>
                      );
                    })()
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-white font-semibold">{money(q.amount)}</span>
                        <span className="chip bg-ink-700 text-mute">{KIND_LABEL[q.kind] ?? q.kind}</span>
                        <span className="text-gold-soft text-[12px] hover:underline">#{q.dealNumber}</span>
                      </div>
                      <div className="text-mute text-[13px] truncate">{q.client} · {q.destination}</div>
                    </>
                  )}
                  {q.status === "pending" && already > 0 && !isCompanyInvoiceTask(q.kind) && (
                    <div className="text-[12px] text-amber-300/90 mt-1">
                      Выдано частями {money(already)} · осталось {money(remainder)}
                    </div>
                  )}
                  {(q.payouts ?? []).length > 0 && (
                    <div className="text-[11px] text-mute mt-1 space-y-0.5">
                      {(q.payouts ?? []).map((p) => (
                        <div key={p.id}>
                          {money(p.amount)} · {paymentMethodLabel(p.methodId)} · {p.issuedBy}
                        </div>
                      ))}
                    </div>
                  )}
                  {q.status === "issued" && <div className="text-[12px] text-mute mt-1"><Clock3 size={12} className="inline mr-1" />{shortDate(q.issuedAt ?? q.createdAt)} {timeOf(q.issuedAt ?? q.createdAt)}{q.issuedBy && ` · ${q.issuedBy}`}{q.issueMethod && ` · ${paymentMethodLabel(q.issueMethod)}`}</div>}
                </button>
                {q.status === "pending" && isCompanyInvoiceTask(q.kind) && (
                  <button
                    onClick={() => openInvoice(q)}
                    className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-3 py-2 hover:bg-white/90"
                  >
                    <ReceiptText size={16} />{" "}
                    {q.kind === "invoice" ? "Выставить счет" : "Проверить оплату"}
                  </button>
                )}
                {q.status === "pending" && !isCompanyInvoiceTask(q.kind) && q.kind !== "atelier" && (
                  <button onClick={() => openIssue(q)} className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-3 py-2 hover:bg-white/90">
                    <Check size={16} /> Выдать
                  </button>
                )}
                {q.status === "issued" && (
                  <button
                    type="button"
                    disabled={reopening === q.id}
                    onClick={() => void reopenItem(q)}
                    className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-ink-600 text-mute hover:text-white hover:border-white/40 px-3 py-2 text-sm font-semibold"
                  >
                    <Undo2 size={15} /> Вернуть
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <Modal
        open={!!issuing}
        onClose={() => setIssuing(null)}
        title={`Выдать ${issuing ? money(issuingRemainder) : ""}`}
      >
        <div className="space-y-4">
          <div>
            <div className="field-label">
              Способ выдачи{" "}
              {issuing?.kind === "tips"
                ? "чаевых"
                : issuing?.kind === "refund"
                  ? "возврата"
                  : issuing?.kind === "salary"
                    ? "зарплаты"
                    : "сдачи"}
            </div>
            <PaymentMethodSelect value={issueMethod} onChange={setIssueMethod} withCertificate={false} />
          </div>
          <div>
            <div className="field-label flex items-center justify-between gap-2">
              <span>Сумма выдачи</span>
              <button
                type="button"
                className="text-[11px] text-gold-soft hover:text-white normal-case tracking-normal font-semibold"
                onClick={() => setIssueAmount(String(Math.round(issuingRemainder)))}
              >
                вся сумма
              </button>
            </div>
            <input
              className="input"
              inputMode="numeric"
              value={issueAmount}
              onChange={(e) => setIssueAmount(e.target.value.replace(/\D/g, ""))}
            />
            <div className="text-[12px] text-mute mt-1">
              Можно выдать частями: остаток {money(Math.max(0, issuingRemainder - (Number(issueAmount) || 0)))} останется в очереди.
            </div>
          </div>
          <div className="flex justify-end">
            <Button disabled={!Number(issueAmount)} onClick={confirmIssue}>
              <Check size={15} /> Подтвердить выдачу
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={!!salaryItem}
        onClose={() => setSalaryItem(null)}
        title={salaryItem ? `Ведомость · ${salaryItem.destination}` : "Ведомость"}
      >
        {salaryItem && (() => {
          const rows = ((salaryItem.metadata?.rows as SalaryRow[] | undefined) ?? []);
          return (
            <div className="space-y-3">
              {rows.length === 0 && (
                <div className="text-mute text-sm py-4 text-center">Ведомость пуста</div>
              )}
              {rows.map((r) => (
                <div key={r.consultant} className="rounded-lg border border-ink-700 bg-ink-900/50">
                  <button
                    type="button"
                    className="w-full px-3 py-2.5 flex items-center justify-between gap-2 text-left"
                    onClick={() =>
                      setSalaryExpanded((prev) => (prev === r.consultant ? null : r.consultant))
                    }
                  >
                    <span className="text-white font-medium">{r.consultant}</span>
                    <span className="text-white font-semibold">{money(r.total)}</span>
                  </button>
                  {salaryExpanded === r.consultant && (
                    <div className="px-3 pb-3 text-[13px] space-y-1.5">
                      {r.days.map((d) => (
                        <div key={d.date} className="flex justify-between text-mute">
                          <span>
                            {shortDate(d.date)} · выручка {money(d.revenue)}
                            {d.floorApplied ? " · ставка" : " · %"}
                          </span>
                          <span className="text-white">{money(d.payout)}</span>
                        </div>
                      ))}
                      <div className="flex justify-between text-mute pt-1 border-t border-ink-700">
                        <span>База за дни</span>
                        <span className="text-white">{money(r.basePay)}</span>
                      </div>
                      <div className="flex justify-between text-mute">
                        <span>
                          Премия за конверсию
                          {r.conversionPct != null ? ` (${r.conversionPct.toFixed(1)}%)` : ""}
                          {pctHint(r.conversionBonusPct, r.periodRevenue)}
                        </span>
                        <span className="text-white">{money(r.conversionBonus)}</span>
                      </div>
                      <div className="flex justify-between text-mute">
                        <span>
                          Премия за UPT{r.upt != null ? ` (${r.upt.toFixed(2)})` : ""}
                          {pctHint(r.uptBonusPct, r.periodRevenue)}
                        </span>
                        <span className="text-white">{money(r.uptBonus)}</span>
                      </div>
                      {/* Штрафы за период — вычитаются из итога (созвон 04.09). */}
                      {!!r.conversionPenalty && (
                        <div className="flex justify-between text-mute">
                          <span>
                            Штраф за конверсию
                            {pctHint(r.conversionPenaltyPct, r.periodRevenue)}
                          </span>
                          <span className="text-red-300">−{money(r.conversionPenalty)}</span>
                        </div>
                      )}
                      {!!r.uptPenalty && (
                        <div className="flex justify-between text-mute">
                          <span>Штраф за UPT{pctHint(r.uptPenaltyPct, r.periodRevenue)}</span>
                          <span className="text-red-300">−{money(r.uptPenalty)}</span>
                        </div>
                      )}
                      <div className="flex justify-between pt-1 border-t border-ink-700">
                        <span className="text-mute">К выплате</span>
                        <span className="text-white font-semibold">{money(r.total)}</span>
                      </div>
                    </div>
                  )}
                </div>
              ))}
              <div className="flex items-center justify-between pt-1">
                <div className="text-mute text-sm">
                  Итого {money(salaryItem.amount)} · выдача частями пишется расходом «Зарплата»
                </div>
                {salaryItem.status === "pending" && (
                  <Button
                    onClick={() => {
                      const item = salaryItem;
                      setSalaryItem(null);
                      openIssue(item);
                    }}
                  >
                    <Check size={15} /> Выдать
                  </Button>
                )}
              </div>
            </div>
          );
        })()}
      </Modal>

      <Modal
        open={!!invoiceItem}
        onClose={() => setInvoiceItem(null)}
        title={
          invoiceItem
            ? invoiceItem.kind === "invoice_check"
              ? `Проверить оплату · #${invoiceItem.dealNumber}`
              : `Выставить счет · #${invoiceItem.dealNumber}`
            : "Счёт"
        }
      >
        <div className="space-y-4">
          {invoiceItem &&
            (() => {
              const { company, buyer } = companyMeta(invoiceItem);
              return (
                <div className="rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2.5 text-[13px]">
                  <div className="text-white font-medium">{company || "Компания"}</div>
                  <div className="text-mute mt-0.5">
                    {[
                      buyer && buyer !== company ? buyer : null,
                      invoiceItem.amount > 0 ? `к оплате ${money(invoiceItem.amount)}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
              );
            })()}
          <div className="grid sm:grid-cols-2 gap-3">
            <label>
              <div className="field-label">Номер счёта</div>
              <input
                className="input"
                value={invoiceNo}
                onChange={(e) => setInvoiceNo(e.target.value)}
                placeholder="СЧ-2026-…"
              />
            </label>
            <label>
              <div className="field-label">Дата счёта</div>
              <input
                type="date"
                className="input"
                value={invoiceDate}
                onChange={(e) => setInvoiceDate(e.target.value)}
              />
            </label>
          </div>
          {invoiceItem?.kind === "invoice" && (
            <div className="text-[12px] text-mute">
              После «Счёт выставлен» сразу появится задача «Проверить оплату» по этой заявке.
            </div>
          )}
          {invoiceError && (
            <div className="text-[13px] text-red-300 bg-red-400/10 border border-red-400/30 rounded-lg px-3 py-2">
              {invoiceError}
            </div>
          )}
          <div className="flex justify-end gap-2">
            {invoiceItem && (
              <Button variant="subtle" onClick={() => void openDealByNumber(invoiceItem.dealNumber)}>
                Заявка #{invoiceItem.dealNumber}
              </Button>
            )}
            {invoiceItem?.kind === "invoice" ? (
              <Button disabled={!invoiceNo.trim() || !invoiceDate} onClick={() => void saveInvoice(false)}>
                <Check size={15} /> Счёт выставлен
              </Button>
            ) : (
              <Button disabled={!invoiceNo.trim() || !invoiceDate} onClick={() => void saveInvoice(true)}>
                <Check size={15} /> Счёт оплачен
              </Button>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        open={!!openDeal || dealLoading || !!dealError}
        onClose={() => {
          setOpenDeal(null);
          setDealError(null);
        }}
        title={openDeal ? `Заявка №${openDeal.number}` : dealLoading ? "Загрузка…" : "Заявка"}
        xl
      >
        {dealLoading && !openDeal && (
          <div className="py-10 text-center text-mute text-sm">Загружаем заявку…</div>
        )}
        {dealError && !openDeal && (
          <div className="py-6 text-center text-amber-300/90 text-sm">{dealError}</div>
        )}
        {openDeal && (
          <DealWorkspace
            deal={openDeal}
            hideBack
            onClose={() => {
              setOpenDeal(null);
              setDealError(null);
            }}
          />
        )}
      </Modal>

      <Modal open={expenseOpen} onClose={() => setExpenseOpen(false)} title="Новый расход">
        <div className="space-y-4">
          <label className="block">
            <div className="field-label">Статья расхода</div>
            <select className="input" value={expenseCategory} onChange={(e) => setExpenseCategory(e.target.value)}>
              {EDWIN_EXPENSE_CATEGORIES.map((category) => <option key={category}>{category}</option>)}
            </select>
          </label>
          <div className="grid sm:grid-cols-2 gap-3">
            <label>
              <div className="field-label">Сумма</div>
              <input className="input" inputMode="numeric" value={expenseAmount} onChange={(e) => setExpenseAmount(e.target.value.replace(/\D/g, ""))} />
            </label>
            <label>
              <div className="field-label">Дата расхода</div>
              <input type="date" className="input" value={expenseDate} onChange={(e) => setExpenseDate(e.target.value)} />
            </label>
          </div>

          {expenseSplits.length === 0 ? (
            <label className="block">
              <div className="field-label">Способ оплаты</div>
              <PaymentMethodSelect
                value={expenseMethod}
                onChange={setExpenseMethod}
                withCertificate={false}
                placeholder={EXPENSE_METHOD_PLACEHOLDER}
                extraMethods={EXPENSE_EXTRA_METHODS}
              />
            </label>
          ) : (
            <div className="space-y-2">
              <div className="field-label">Способ оплаты — по частям</div>
              {expenseSplits.map((row) => (
                <div key={row.id} className="flex gap-2 items-center">
                  <PaymentMethodSelect
                    value={row.methodId}
                    withCertificate={false}
                    className="input flex-1"
                    placeholder={EXPENSE_METHOD_PLACEHOLDER}
                    extraMethods={EXPENSE_EXTRA_METHODS}
                    onChange={(methodId) =>
                      setExpenseSplits((prev) => prev.map((r) => (r.id === row.id ? { ...r, methodId } : r)))
                    }
                  />
                  <input
                    className="input w-[120px]"
                    inputMode="numeric"
                    placeholder="0"
                    value={row.amount}
                    onChange={(e) =>
                      setExpenseSplits((prev) =>
                        prev.map((r) => (r.id === row.id ? { ...r, amount: e.target.value.replace(/\D/g, "") } : r))
                      )
                    }
                  />
                  <button
                    type="button"
                    className="text-mute hover:text-white"
                    onClick={() => setExpenseSplits((prev) => prev.filter((r) => r.id !== row.id))}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              <div className="text-[12px] text-mute">
                Разбито {money(splitsTotal)} из {money(Number(expenseAmount) || 0)}
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={addSplit}
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-gold-soft hover:text-white"
          >
            <Plus size={15} /> Добавить способ оплаты
          </button>

          <label className="block">
            <div className="field-label">Комментарий</div>
            <input className="input" value={expenseComment} onChange={(e) => setExpenseComment(e.target.value)} />
          </label>
          {expenseError && (
            <div className="text-[13px] text-red-300 bg-red-400/10 border border-red-400/30 rounded-lg px-3 py-2">
              {expenseError}
            </div>
          )}
          <div className="flex justify-end">
            <Button
              disabled={
                !Number(expenseAmount) ||
                (expenseSplits.length === 0 ? !expenseMethod : expenseSplits.some((r) => !r.methodId))
              }
              onClick={() => void saveExpense()}
            >
              Сохранить расход
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
