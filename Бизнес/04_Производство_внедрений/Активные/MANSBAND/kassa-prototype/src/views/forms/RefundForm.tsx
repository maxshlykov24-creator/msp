import { useState } from "react";
import { Search, UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { lineTotal } from "../../components/ProductPicker";
import {
  CommentField,
  ConsultantFields,
  FormShell,
  ReturnBlock,
  SectionTitle,
  StageActions,
  useSaved,
  type ConsultantData,
  type ReturnInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone, money } from "../../lib/format";
import type { CartItem, Deal } from "../../data/types";

export function RefundForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, addQueueItem, nextNumber, findByPhone } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [phone, setPhone] = useState("");
  const [source, setSource] = useState<Deal | null>(null);
  const [selected, setSelected] = useState<Record<number, boolean>>({});

  const [returnInfo, setReturnInfo] = useState<ReturnInfo>({ status: "issued", destination: "" });
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Взято в работу");

  function findSource() {
    const found = findByPhone(phone);
    if (found) {
      setSource(found);
      const all: Record<number, boolean> = {};
      found.items.forEach((_, i) => (all[i] = true));
      setSelected(all);
    } else {
      setSource(null);
    }
  }

  const returnItems: CartItem[] = source ? source.items.filter((_, i) => selected[i]) : [];
  const refundAmount = returnItems.reduce((s, it) => s + lineTotal(it), 0);

  const baseFilled = !!(source && returnItems.length > 0 && (returnInfo.status === "issued" || returnInfo.destination.trim()));

  function save(s: string) {
    if (!source) return;
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "return",
      kind: "refund",
      consultant: consultants.consultant,
      clientName: source.clientName,
      clientPhone: source.clientPhone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: source.channel,
      purpose: source.purpose,
      linkedDealNumber: source.number,
      items: returnItems.map((it) => ({ ...it, price: -Math.abs(it.price) })),
      payments: [],
      stage: s,
      comment,
      returnStatus: returnInfo.status,
      returnDestination: returnInfo.status === "pending" ? returnInfo.destination : undefined,
      total: -refundAmount,
      paid: 0,
    });
    if (returnInfo.status === "pending") {
      addQueueItem({
        id: crypto.randomUUID(),
        kind: "refund",
        dealNumber: meta.number,
        client: source.clientName,
        amount: refundAmount,
        destination: returnInfo.destination,
        status: "pending",
        createdAt: meta.createdAt,
      });
    }
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Возврат"
      subtitle="Возврат · привязан к исходной заявке"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      footer={
        <StageActions
          stages={["Взято в работу"]}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successStage="Успех"
          successDisabled={!baseFilled}
          successHint={!source ? "Найдите исходную заявку по телефону" : !returnItems.length ? "Выберите позиции к возврату" : undefined}
          onSuccess={() => save("Успех")}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
      </Card>

      <Card>
        <SectionTitle>Исходная заявка</SectionTitle>
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <Field label="Телефон клиента" required>
              <input
                className="input"
                inputMode="tel"
                value={phone}
                placeholder="+7 (___) ___-__-__"
                onChange={(e) => setPhone(formatPhone(e.target.value))}
              />
            </Field>
          </div>
          <button
            type="button"
            onClick={findSource}
            className="inline-flex items-center gap-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-white font-semibold px-3 py-2.5"
          >
            <Search size={16} /> Найти
          </button>
        </div>
        {phone && !source && (
          <div className="mt-3 text-[12px] text-amber-300/80">Заявка по телефону не найдена</div>
        )}
        {source && (
          <div className="mt-3 flex items-center gap-2 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-3 py-2">
            <UserCheck size={13} /> Найдена заявка #{source.number} · {source.clientName} · {money(source.total)}
          </div>
        )}
      </Card>

      {source && (
        <Card>
          <SectionTitle>Позиции к возврату</SectionTitle>
          <div className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
            {source.items.map((it, i) => (
              <label key={i} className="flex items-center gap-3 px-3 py-2.5 bg-ink-900/50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!selected[i]}
                  onChange={(e) => setSelected((prev) => ({ ...prev, [i]: e.target.checked }))}
                />
                <span className="flex-1 text-[14px] text-white">{it.name} × {it.qty}</span>
                <span className="font-semibold text-white">{it.noPrice ? "—" : money(lineTotal(it))}</span>
              </label>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between rounded-lg bg-ink-900 border border-ink-700 px-4 py-3">
            <span className="text-mute text-sm">Сумма к возврату</span>
            <span className="text-white font-bold text-lg">{money(refundAmount)}</span>
          </div>
          <div className="mt-4">
            <ReturnBlock amount={refundAmount} info={returnInfo} onChange={setReturnInfo} />
          </div>
        </Card>
      )}

      <Card>
        <SectionTitle>Комментарий</SectionTitle>
        <CommentField value={comment} onChange={setComment} />
      </Card>

      {toast}
    </FormShell>
  );
}
