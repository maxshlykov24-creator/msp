import { useEffect, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { useStore } from "../../store";
import { Button, Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { ReturnItemsSelector } from "../../components/ReturnItemsSelector";
import {
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentSection,
  ReturnBlock,
  SectionTitle,
  StageActions,
  changeDealFields,
  changeTipsMissing,
  lastPaymentDate,
  returnDealFields,
  returnPayoutMissing,
  tipsDealFields,
  useSaved,
  type ChangeInfo,
  type ConsultantData,
  type ReturnInfo,
  type TipsInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone, money } from "../../lib/format";
import type { CartItem, Deal, Payment } from "../../data/types";
import { USE_MOCK } from "../../api/client";

export function ExchangeForm({
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

  const [returnItems, setReturnItems] = useState<CartItem[]>([]);
  const [newItems, setNewItems] = useState<CartItem[]>([]);

  const [payments, setPayments] = useState<Payment[]>([]);
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const [returnInfo, setReturnInfo] = useState<ReturnInfo>({ status: "issued", destination: "" });
  const [manualCheck, setManualCheck] = useState<string[]>([]);
  const [comment, setComment] = useState("");

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
    setReturnItems([]);
  }

  const returnSum = cartTotal(returnItems);
  const newSum = cartTotal(newItems);
  const diff = newSum - returnSum;

  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const change = diff > 0 ? Math.max(0, paid - diff) : 0;
  const refundAmount = diff < 0 ? -diff : 0;

  const missingRequired = [
    !source && "Исходная заявка",
    returnItems.length === 0 && "Позиции к возврату",
    newItems.length === 0 && "Новые позиции",
    diff > 0 && paid < diff && `Доплата ещё ${money(diff - paid)}`,
    ...changeTipsMissing(paid, Math.max(0, diff), changeInfo, tips),
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
      kind: "exchange",
      consultant: consultants.consultant,
      clientName: source.clientName,
      clientPhone: source.clientPhone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: source.channel,
      purpose: source.purpose,
      linkedDealNumber: source.number,
      items: [
        ...returnItems.map((it) => ({
          ...it,
          price: -Math.abs(it.price),
          isReturn: true,
          name: `${it.name} (возврат)`,
        })),
        ...newItems,
      ],
      payments,
      stage: s,
      comment,
      paymentDate: lastPaymentDate(payments),
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      ...returnDealFields(refundAmount, returnInfo),
      total: diff,
      paid: diff > 0 ? paid : 0,
    });
    if (USE_MOCK && refundAmount > 0 && returnInfo.status === "pending") {
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
      title="Обмен"
      subtitle={source ? "Новая заявка" : "Обмен"}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Успех", "Провал"]}
          stage="Успех"
          onStageChange={() => {}}
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
            selected={returnItems}
            onChange={setReturnItems}
            onManualWithoutBarcode={setManualCheck}
          />
          {manualCheck.length > 0 && (
            <div className="mt-3 text-[12px] text-mute">
              Без штрихкода: {manualCheck.length} поз. — после сохранения задача на проверку консультанту.
            </div>
          )}
          <div className="mt-3 flex items-center justify-between text-sm">
            <span className="text-mute">Возврат на сумму</span>
            <span className="text-white font-semibold">{money(returnSum)}</span>
          </div>
        </Card>
      )}

      <Card>
        <SectionTitle>Новые позиции к выдаче</SectionTitle>
        <ProductPicker items={newItems} onChange={setNewItems} />
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="rounded-lg bg-ink-900 border border-ink-700 px-4 py-3 flex items-center justify-between">
            <span className="text-mute text-sm">Новые товары</span>
            <span className="text-white font-semibold">{money(newSum)}</span>
          </div>
          <div
            className={`rounded-lg px-4 py-3 flex items-center justify-between border ${
              diff >= 0 ? "bg-ink-900 border-ink-700" : "bg-amber-400/10 border-amber-400/40"
            }`}
          >
            <span className="text-mute text-sm">{diff >= 0 ? "К доплате" : "К возврату"}</span>
            <span className="text-white font-bold">{money(Math.abs(diff))}</span>
          </div>
        </div>
      </Card>

      {diff > 0 && (
        <Card>
          <SectionTitle>Доплата</SectionTitle>
          <PaymentSection
            total={diff}
            payments={payments}
            onPayments={setPayments}
            consultant={consultants.consultant}
            changeInfo={changeInfo}
            onChangeInfo={setChangeInfo}
            tips={tips}
            onTips={setTips}
          />
        </Card>
      )}

      {refundAmount > 0 && (
        <Card>
          <SectionTitle>Возврат клиенту</SectionTitle>
          <ReturnBlock amount={refundAmount} info={returnInfo} onChange={setReturnInfo} />
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
