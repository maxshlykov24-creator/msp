import { useEffect, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { useStore } from "../../store";
import { Button, Card, Field } from "../../components/ui";
import { cartTotal } from "../../components/ProductPicker";
import { ReturnItemsSelector } from "../../components/ReturnItemsSelector";
import {
  CommentField,
  ConsultantFields,
  FormShell,
  ReturnBlock,
  SectionTitle,
  StageActions,
  returnDealFields,
  returnPayoutMissing,
  useSaved,
  type ConsultantData,
  type ReturnInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone, money } from "../../lib/format";
import type { CartItem, Deal } from "../../data/types";
import { USE_MOCK } from "../../api/client";

export function RefundForm({
  onDone,
  sourceDeal,
}: {
  onDone: () => void;
  sourceDeal?: Deal | null;
}) {
  const { activeStore, activeConsultant, addDeal, addQueueItem, nextNumber, findByPhone, deals } =
    useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
  });
  const [phone, setPhone] = useState(sourceDeal?.clientPhone ?? "");
  const [source, setSource] = useState<Deal | null>(() => sourceDeal ?? null);
  const [items, setItems] = useState<CartItem[]>([]);

  const [returnInfo, setReturnInfo] = useState<ReturnInfo>({ status: "issued", destination: "" });
  const [manualCheck, setManualCheck] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Успех");

  // Исходная подтянулась асинхронно (NewDeal fetch) — подставляем.
  useEffect(() => {
    if (sourceDeal) setSource(sourceDeal);
  }, [sourceDeal]);

  function openSourceDeal(number: number) {
    window.location.hash = `board/all/all/${number}`;
  }

  function findSource() {
    const found =
      findByPhone(phone) ??
      deals.find((d) => d.clientPhone === phone && (d.stage === "Успех" || d.stage === "Провал"));
    setSource(found ?? null);
    setItems([]);
  }

  const refundAmount = cartTotal(items);

  const missingRequired = [
    !source && "Исходная заявка",
    items.length === 0 && "Позиции к возврату",
    ...returnPayoutMissing(refundAmount, returnInfo),
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

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
      items: items.map((it) => ({
        ...it,
        price: -Math.abs(it.price),
        isReturn: true,
      })),
      payments: [],
      stage: s,
      comment,
      ...returnDealFields(refundAmount, returnInfo),
      total: -refundAmount,
      paid: 0,
    });
    if (USE_MOCK && returnInfo.status === "pending") {
      addQueueItem({
        id: crypto.randomUUID(),
        kind: "refund",
        dealNumber: meta.number,
        client: source.clientName,
        amount: refundAmount,
        destination: returnInfo.destination,
        status: "pending",
        issuedAmount: 0,
        payouts: [],
        createdAt: meta.createdAt,
      });
    }
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      onBack={onDone}
      title="Возврат"
      subtitle={source ? "Новая заявка" : "Возврат"}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Успех", "Провал"]}
          stage={stage === "Взято в работу" ? "Успех" : stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          successStage="Успех"
          successDisabled={!baseFilled}
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
        {source ? (
          <div className="flex flex-wrap items-center gap-2 justify-between">
            <div className="text-[13px] text-white min-w-0">
              <span className="font-semibold">#{source.number}</span>
              <span className="text-mute">
                {" "}
                · {source.clientName} · {formatPhone(source.clientPhone)} · {money(source.total)}
              </span>
            </div>
            <Button variant="subtle" onClick={() => openSourceDeal(source.number)}>
              <ExternalLink size={14} /> Открыть исходную
            </Button>
          </div>
        ) : (
          <>
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
          </>
        )}
      </Card>

      {source && (
        <Card>
          <SectionTitle>Позиции к возврату</SectionTitle>
          <ReturnItemsSelector
            original={source.items.filter((item) => !item.isReturn && item.price >= 0)}
            selected={items}
            onChange={setItems}
            onManualWithoutBarcode={setManualCheck}
          />
          {manualCheck.length > 0 && (
            <div className="mt-3 text-[12px] text-mute">
              Без штрихкода: {manualCheck.length} поз. — после сохранения задача на проверку консультанту.
            </div>
          )}
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
