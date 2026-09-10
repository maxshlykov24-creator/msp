import { useEffect, useMemo, useState } from "react";
import { FileText, Scissors, Check, Building2, Send, Undo2 } from "lucide-react";
import type { Deal, QueueItem } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { money, shortDate, timeOf } from "../lib/format";
import { Button, Modal } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { canSeeFinanceQueues } from "../auth/roles";
import { DealWorkspace } from "./DealWorkspace";
import { Hint } from "../lib/hints";

type CompanyPatch = {
  documentsStatus?: "pending" | "ready" | "handed";
  atelierStatus?: "pending" | "paid";
};

/** Виды очереди Миши в порядке цепочки. */
const MISHA_KINDS = ["documents", "documents_hand", "atelier"] as const;
type MishaKind = (typeof MISHA_KINDS)[number];

const STEP_LABEL: Record<MishaKind, string> = {
  documents: "Подготовить документы",
  documents_hand: "Передать документы",
  atelier: "Оплатить ателье",
};

const ACTION_LABEL: Record<MishaKind, string> = {
  documents: "Документы подготовлены",
  documents_hand: "Документы переданы",
  atelier: "Ателье оплачено",
};

const PATCH_BY_KIND: Record<MishaKind, CompanyPatch> = {
  documents: { documentsStatus: "ready" },
  documents_hand: { documentsStatus: "handed" },
  atelier: { atelierStatus: "paid" },
};

const KIND_ORDER: MishaKind[] = ["documents", "documents_hand", "atelier"];

function isMishaKind(kind: string): kind is MishaKind {
  return (MISHA_KINDS as readonly string[]).includes(kind);
}

/** Первая незакрытая задача заявки в порядке цепочки — только она доступна. */
function actionableIdForDeal(dealNumber: number, rows: QueueItem[]): string | null {
  const pending = rows.filter(
    (row) => row.dealNumber === dealNumber && row.status === "pending" && isMishaKind(row.kind)
  );
  for (const kind of KIND_ORDER) {
    const hit = pending.find((row) => row.kind === kind);
    if (hit) return hit.id;
  }
  return null;
}

function companyOf(item: QueueItem): string {
  const m = item.metadata ?? {};
  return (
    (typeof m.companyName === "string" && m.companyName.trim()) ||
    item.destination ||
    item.client ||
    ""
  );
}

function invoiceOf(item: QueueItem, deal?: Deal): string {
  const m = item.metadata ?? {};
  return (typeof m.invoiceNo === "string" && m.invoiceNo.trim()) || deal?.invoiceNo || "";
}

function atelierAmountOf(item: QueueItem): number {
  const m = item.metadata ?? {};
  if (item.kind === "atelier" && item.amount > 0) return item.amount;
  if (typeof m.atelierAmount === "number" && m.atelierAmount > 0) return m.atelierAmount;
  return 0;
}

function FilterChip({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
        active
          ? "bg-gold/15 text-gold-soft border border-gold/40"
          : "text-mute hover:bg-ink-800 border border-transparent"
      }`}
    >
      {label}
      {count != null && count > 0 && <span className="ml-1.5 opacity-70">{count}</span>}
    </button>
  );
}

/**
 * Очередь Миши: после оплаты счёта падают все шаги сразу, кнопки — по порядку.
 * Список как у задач: таблица, чипы видов, в работе только текущий шаг заявки.
 */
export function MishaQueue() {
  const { queue, deals, reopenQueueItem } = useStore();
  const { user } = useAuth();
  const allowed = canSeeFinanceQueues(user?.role, user?.login, user?.name);
  const [status, setStatus] = useState("pending");
  const [kindFilter, setKindFilter] = useState<"" | MishaKind>("");
  const [rows, setRows] = useState<QueueItem[]>(queue as QueueItem[]);
  const [busy, setBusy] = useState<string | null>(null);
  const [openDeal, setOpenDeal] = useState<Deal | null>(null);
  const [dealLoading, setDealLoading] = useState(false);
  const [dealError, setDealError] = useState<string | null>(null);

  useEffect(() => setRows(queue as QueueItem[]), [queue]);

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

  const mishaRows = useMemo(
    () => rows.filter((row) => isMishaKind(row.kind)),
    [rows]
  );

  const pending = mishaRows.filter((row) => row.status === "pending");
  const atelierSum = pending
    .filter((row) => row.kind === "atelier")
    .reduce((sum, row) => sum + row.amount, 0);

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of mishaRows) {
      if (status !== "all" && row.status !== status) continue;
      counts[row.kind] = (counts[row.kind] ?? 0) + 1;
    }
    return counts;
  }, [mishaRows, status]);

  const items = useMemo(() => {
    let list = mishaRows.filter((row) => status === "all" || row.status === status);
    if (kindFilter) list = list.filter((row) => row.kind === kindFilter);
    // В работе без фильтра вида — только шаг, который можно закрыть сейчас.
    if (status === "pending" && !kindFilter) {
      list = list.filter((row) => actionableIdForDeal(row.dealNumber, rows) === row.id);
    }
    return list.sort((a, b) => {
      if (a.dealNumber !== b.dealNumber) return b.dealNumber - a.dealNumber;
      return KIND_ORDER.indexOf(a.kind as MishaKind) - KIND_ORDER.indexOf(b.kind as MishaKind);
    });
  }, [mishaRows, status, kindFilter, rows]);

  function dealOf(number: number): Deal | undefined {
    return deals.find((d) => d.number === number);
  }

  function stepIndex(dealNumber: number, kind: MishaKind): string {
    const dealSteps = mishaRows.filter((row) => row.dealNumber === dealNumber);
    const total = Math.max(dealSteps.length, KIND_ORDER.length);
    return `${KIND_ORDER.indexOf(kind) + 1} / ${total}`;
  }

  async function closeStep(item: QueueItem) {
    const kind = item.kind as MishaKind;
    if (actionableIdForDeal(item.dealNumber, rows) !== item.id) return;
    setBusy(item.id);
    try {
      if (!USE_MOCK) {
        if (kind === "atelier") {
          await api.patch(`/queue/${item.id}/issue`, { methodId: "cash", amount: item.amount });
        }
        await api.patch(`/deals/${item.dealNumber}/company`, PATCH_BY_KIND[kind]);
      }
      setRows((prev) =>
        prev.map((row) =>
          row.id === item.id
            ? {
                ...row,
                status: "issued",
                issuedAmount: row.amount,
                issuedAt: new Date().toISOString(),
              }
            : row
        )
      );
    } finally {
      setBusy(null);
    }
  }

  async function reopenStep(item: QueueItem) {
    if (
      !confirm(
        `Вернуть «${STEP_LABEL[item.kind as MishaKind] ?? item.kind}» #${item.dealNumber} в работу?`
      )
    ) {
      return;
    }
    setBusy(item.id);
    try {
      await reopenQueueItem(item.id);
      setRows((prev) =>
        prev.map((row) =>
          row.id === item.id
            ? {
                ...row,
                status: "pending",
                issuedAmount: 0,
                payouts: [],
                issuedAt: undefined,
                issuedBy: undefined,
                issueMethod: undefined,
              }
            : row
        )
      );
    } finally {
      setBusy(null);
    }
  }

  if (!allowed) {
    return (
      <div className="max-w-3xl mx-auto card py-10 text-center text-mute">
        Очередь Миши доступна только Эдвину, РОПу и администратору
      </div>
    );
  }

  const actionablePending = pending.filter(
    (row) => actionableIdForDeal(row.dealNumber, rows) === row.id
  ).length;

  return (
    <div className="max-w-7xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Building2 className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Очередь Миши</h1>
      </div>
      <Hint>
        Документы и ателье по продажам компаний. В работе виден только текущий шаг заявки. Следующий
        откроется, когда закроешь предыдущий. Консультанту эта очередь не показывается.
      </Hint>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="inline-flex border border-ink-700 rounded-lg overflow-hidden">
          {(
            [
              ["pending", "В работе", actionablePending],
              ["issued", "Закрытые", mishaRows.filter((r) => r.status === "issued").length],
              ["all", "Все", mishaRows.length],
            ] as const
          ).map(([id, label, count]) => (
            <button
              key={id}
              type="button"
              className={`px-4 py-2 text-sm ${
                status === id ? "bg-gold text-ink-950 font-semibold" : "text-mute hover:text-white"
              }`}
              onClick={() => setStatus(id)}
            >
              {label}
              {count > 0 && <span className="ml-2 opacity-80">{count}</span>}
            </button>
          ))}
        </div>
        <span className="text-[12px] text-mute">
          {items.length}
          {atelierSum > 0 ? ` · ателье ${money(atelierSum)}` : ""}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-4">
        <FilterChip
          active={!kindFilter}
          onClick={() => setKindFilter("")}
          label="Все шаги"
        />
        {KIND_ORDER.map((kind) => (
          <FilterChip
            key={kind}
            active={kindFilter === kind}
            onClick={() => setKindFilter(kind)}
            label={STEP_LABEL[kind]}
            count={kindCounts[kind] ?? 0}
          />
        ))}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[860px]">
            <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Шаг</th>
                <th className="text-left px-3 py-2.5 font-medium">Заявка</th>
                <th className="text-left px-3 py-2.5 font-medium">Компания</th>
                <th className="text-left px-3 py-2.5 font-medium">Счёт</th>
                <th className="text-left px-3 py-2.5 font-medium">Когда</th>
                <th className="px-3 py-2.5 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {items.map((item) => {
                const kind = item.kind as MishaKind;
                const deal = dealOf(item.dealNumber);
                const actionable =
                  item.status === "pending" &&
                  actionableIdForDeal(item.dealNumber, rows) === item.id;
                const locked = item.status === "pending" && !actionable;
                const invoice = invoiceOf(item, deal);
                const atelier = atelierAmountOf(item);
                const when = item.status === "issued" ? item.issuedAt ?? item.createdAt : item.createdAt;
                return (
                  <tr
                    key={item.id}
                    className={`hover:bg-ink-800/40 cursor-pointer transition ${
                      locked ? "opacity-55" : ""
                    }`}
                    onClick={() => void openDealByNumber(item.dealNumber)}
                  >
                    <td className="px-3 py-3 align-top">
                      <div className="flex items-center gap-2">
                        <span className="w-8 h-8 rounded-lg grid place-items-center bg-white/10 text-white shrink-0">
                          {kind === "atelier" ? (
                            <Scissors size={15} />
                          ) : kind === "documents_hand" ? (
                            <Send size={15} />
                          ) : (
                            <FileText size={15} />
                          )}
                        </span>
                        <div>
                          <div className="text-white font-medium text-[13px]">{STEP_LABEL[kind]}</div>
                          <div className="text-[11px] text-mute mt-0.5">{stepIndex(item.dealNumber, kind)}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3 align-top text-white text-[13px] whitespace-nowrap">
                      #{item.dealNumber}
                    </td>
                    <td className="px-3 py-3 align-top min-w-0">
                      <div className="text-white text-[13px] leading-snug line-clamp-2">
                        {companyOf(item) || "—"}
                      </div>
                      {locked && (
                        <div className="text-[11px] text-mute mt-0.5">Сначала предыдущий шаг</div>
                      )}
                    </td>
                    <td className="px-3 py-3 align-top text-[13px] text-mute-soft whitespace-nowrap">
                      {invoice ? `счёт ${invoice}` : "—"}
                      {atelier > 0 && (
                        <div className="text-white tabular-nums mt-0.5">{money(atelier)}</div>
                      )}
                    </td>
                    <td className="px-3 py-3 align-top text-[12px] text-mute tabular-nums whitespace-nowrap">
                      <div>{shortDate(when)}</div>
                      <div className="text-[11px] text-mute/80">{timeOf(when)}</div>
                    </td>
                    <td className="px-3 py-3 align-top text-right" onClick={(e) => e.stopPropagation()}>
                      {actionable && (
                        <Button
                          variant="subtle"
                          className="py-1.5 px-2.5 text-[12px]"
                          disabled={busy === item.id}
                          onClick={() => void closeStep(item)}
                        >
                          <Check size={14} /> {ACTION_LABEL[kind]}
                        </Button>
                      )}
                      {item.status === "issued" && (
                        <Button
                          variant="subtle"
                          className="py-1.5 px-2.5 text-[12px]"
                          disabled={busy === item.id}
                          onClick={() => void reopenStep(item)}
                        >
                          <Undo2 size={14} /> Вернуть
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {items.length === 0 && <div className="py-10 text-center text-mute">Задач в очереди нет</div>}
      </div>

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
    </div>
  );
}
