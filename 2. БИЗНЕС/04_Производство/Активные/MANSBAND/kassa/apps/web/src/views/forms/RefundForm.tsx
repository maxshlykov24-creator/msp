import { useEffect, useState } from "react";
import { useStore } from "../../store";
import { Card } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { ReturnItemsSelector } from "../../components/ReturnItemsSelector";
import { SourceDealSearch } from "../../components/SourceDealSearch";
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
import { money } from "../../lib/format";
import type { CartItem, Deal } from "../../data/types";
import { USE_MOCK } from "../../api/client";

export function RefundForm({
  onDone,
  sourceDeal,
  embedded = false,
}: {
  onDone: () => void;
  sourceDeal?: Deal | null;
  embedded?: boolean;
}) {
  const { activeStore, activeConsultant, addDeal, addQueueItem, nextNumber, deals } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
  });
  const [clientName, setClientName] = useState(sourceDeal?.clientName ?? "");
  const [clientPhone, setClientPhone] = useState(sourceDeal?.clientPhone ?? "");
  const [source, setSource] = useState<Deal | null>(() => sourceDeal ?? null);
  const [unlinked, setUnlinked] = useState(false);
  const [items, setItems] = useState<CartItem[]>([]);

  const [returnInfo, setReturnInfo] = useState<ReturnInfo>({ status: "issued", destination: "" });
  const [manualCheck, setManualCheck] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Успех");

  // Исходная подтянулась асинхронно (NewDeal fetch) — подставляем.
  useEffect(() => {
    if (sourceDeal) setSource(sourceDeal);
  }, [sourceDeal]);

  const refundAmount = cartTotal(items);
  const phoneDigits = clientPhone.replace(/\D/g, "");

  const missingRequired = [
    !source && !unlinked && "Исходная заявка",
    unlinked && !clientName.trim() && "Имя клиента",
    unlinked && phoneDigits.length < 10 && "Телефон клиента",
    items.length === 0 && "Позиции к возврату",
    ...returnPayoutMissing(refundAmount, returnInfo),
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    if (!source && !unlinked) return;
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "return",
      kind: "refund",
      consultant: consultants.consultant,
      clientName: source?.clientName || clientName.trim() || "Клиент",
      clientPhone: source?.clientPhone || clientPhone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: source?.channel,
      purpose: source?.purpose,
      linkedDealNumber: source?.number,
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
        client: source?.clientName || clientName.trim() || "Клиент",
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
      embedded={embedded}
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
        <SourceDealSearch
          deals={deals}
          source={source}
          unlinked={unlinked}
          clientName={clientName}
          onClientName={setClientName}
          onPhone={setClientPhone}
          onPick={(deal) => {
            setSource(deal);
            setUnlinked(false);
            setClientName(deal.clientName);
            setClientPhone(deal.clientPhone);
            setItems([]);
          }}
          onUnlinked={(phone, name) => {
            setSource(null);
            setUnlinked(true);
            setClientPhone(phone);
            setClientName(name);
            setItems([]);
          }}
          onClear={() => {
            setSource(null);
            setUnlinked(false);
            setItems([]);
          }}
        />
      </Card>

      {(source || unlinked) && (
        <Card>
          <SectionTitle>Позиции к возврату</SectionTitle>
          {source && source.items.some((item) => !item.isReturn && item.price >= 0) ? (
          <ReturnItemsSelector
            original={source.items.filter((item) => !item.isReturn && item.price >= 0)}
            selected={items}
            onChange={setItems}
            onManualWithoutBarcode={setManualCheck}
          />
          ) : (
            <ProductPicker items={items} onChange={setItems} />
          )}
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
