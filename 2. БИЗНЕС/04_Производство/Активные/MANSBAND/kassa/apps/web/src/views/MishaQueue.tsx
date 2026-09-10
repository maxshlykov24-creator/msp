import { useEffect, useMemo, useState } from "react";
import { FileText, Scissors, Check, Building2, Send, Undo2 } from "lucide-react";
import type { Deal, QueueItem } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { money, shortDate } from "../lib/format";
import { Button, Modal, StatTile, Select, opts } from "../components/ui";
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

/** Первая незакрытая задача заявки в порядке цепочки — только она доступна. */
function actionableIdForDeal(dealNumber: number, rows: QueueItem[]): string | null {
  const pending = rows.filter(
    (row) =>
      row.dealNumber === dealNumber &&
      row.status === "pending" &&
      (MISHA_KINDS as readonly string[]).includes(row.kind)
  );
  for (const kind of KIND_ORDER) {
    const hit = pending.find((row) => row.kind === kind);
    if (hit) return hit.id;
  }
  return null;
}

function metaLine(item: QueueItem, deal?: Deal): string {
  const m = item.metadata ?? {};
  const company =
    (typeof m.companyName === "string" && m.companyName.trim()) ||
    item.destination ||
    item.client ||
    "";
  const invoice =
    (typeof m.invoiceNo === "string" && m.invoiceNo.trim()) ||
    deal?.invoiceNo ||
    "";
  const amount =
    item.kind === "atelier" && item.amount > 0
      ? money(item.amount)
      : typeof m.atelierAmount === "number" && m.atelierAmount > 0 && item.kind === "atelier"
        ? money(m.atelierAmount)
        : null;
  return [company || null, invoice ? `счёт ${invoice}` : null, amount].filter(Boolean).join(" · ");
}

/**
 * Очередь Миши: после оплаты счёта падают все шаги сразу, кнопки — по порядку.
 */
export function MishaQueue() {
  const { queue, deals, reopenQueueItem } = useStore();
  const { user } = useAuth();
  const allowed = canSeeFinanceQueues(user?.role, user?.login, user?.name);
  const [status, setStatus] = useState("pending");
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

  const items = useMemo(
    () =>
      rows
        .filter(
          (row) =>
            (MISHA_KINDS as readonly string[]).includes(row.kind) &&
            (status === "all" || row.status === status)
        )
        .sort((a, b) => {
          if (a.dealNumber !== b.dealNumber) return b.dealNumber - a.dealNumber;
          return (
            KIND_ORDER.indexOf(a.kind as MishaKind) - KIND_ORDER.indexOf(b.kind as MishaKind)
          );
        }),
    [rows, status]
  );
  const pending = items.filter((row) => row.status === "pending");
  const atelierSum = pending
    .filter((row) => row.kind === "atelier")
    .reduce((sum, row) => sum + row.amount, 0);

  function dealOf(number: number): Deal | undefined {
    return deals.find((d) => d.number === number);
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

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Building2 className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Очередь Миши</h1>
      </div>
      <Hint>
        Документы и ателье по продажам компаний. Шаги по заявке идут по порядку: сначала
        предыдущий, потом следующий. Консультанту эта очередь не показывается.
      </Hint>

      <div className="grid sm:grid-cols-3 gap-3 mb-5">
        <StatTile label="В работе" value={String(pending.length)} tone="amber" />
        <StatTile
          label="Документов ждёт"
          value={String(pending.filter((row) => row.kind !== "atelier").length)}
        />
        <StatTile label="Ателье к оплате" value={money(atelierSum)} tone="green" />
      </div>

      <div className="card p-3 mb-4">
        <Select
          size="sm"
          className="w-[200px]"
          value={status}
          onChange={setStatus}
          options={opts(["pending", "В работе"], ["issued", "Закрытые"], ["all", "Все"])}
        />
      </div>

      <div className="space-y-2.5">
        {items.length === 0 && (
          <div className="card text-center text-mute py-8">Задач в очереди нет</div>
        )}
        {items.map((item) => {
          const kind = item.kind as MishaKind;
          const deal = dealOf(item.dealNumber);
          const actionable =
            item.status === "pending" && actionableIdForDeal(item.dealNumber, rows) === item.id;
          const locked = item.status === "pending" && !actionable;
          const line = metaLine(item, deal);
          return (
            <div
              key={item.id}
              className={`card p-4 flex flex-wrap items-center gap-3 ${
                locked ? "opacity-55" : ""
              }`}
            >
              <div className="w-10 h-10 rounded-xl grid place-items-center bg-white/10 text-white">
                {kind === "atelier" ? (
                  <Scissors size={18} />
                ) : kind === "documents_hand" ? (
                  <Send size={18} />
                ) : (
                  <FileText size={18} />
                )}
              </div>
              <button
                className="flex-1 min-w-[200px] text-left"
                onClick={() => void openDealByNumber(item.dealNumber)}
              >
                <div className="text-white font-semibold text-[14px]">
                  {STEP_LABEL[kind]} · #{item.dealNumber}
                </div>
                {line && <div className="text-mute text-[13px] mt-0.5 truncate">{line}</div>}
                {locked && (
                  <div className="text-[12px] text-mute mt-0.5">Сначала предыдущий шаг</div>
                )}
                {item.status === "issued" && (
                  <div className="text-[12px] text-mute mt-0.5">
                    закрыто · {shortDate(item.issuedAt ?? item.createdAt)}
                  </div>
                )}
              </button>
              {actionable && (
                <Button disabled={busy === item.id} onClick={() => void closeStep(item)}>
                  <Check size={15} /> {ACTION_LABEL[kind]}
                </Button>
              )}
              {item.status === "issued" && (
                <Button
                  variant="subtle"
                  disabled={busy === item.id}
                  onClick={() => void reopenStep(item)}
                >
                  <Undo2 size={15} /> Вернуть
                </Button>
              )}
            </div>
          );
        })}
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
