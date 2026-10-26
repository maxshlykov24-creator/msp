import { DatePicker } from "../../components/DatePicker";
import { defaultHoldUntil, isHoldStage } from "@kassa/shared";
import { useRef, useState } from "react";
import { useStore } from "../../store";
import { Card, Field, StageBadge } from "../../components/ui";
import { AtelierAmountField } from "../../components/AtelierAmountField";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
import {
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentSection,
  SectionTitle,
  SourceFields,
  StageActions,
  SummaryBar,
  TotalsBlock,
  calcDiscount,
  changeDealFields,
  changeTipsMissing,
  lastPaymentDate,
  movementRequiredGap,
  tipsDealFields,
  useAutoReserveUntil,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone } from "../../lib/format";
import type { CartItem, Deal, Payment } from "../../data/types";

/** При создании компании — только «Товар отложен»; счёт Эдвину уходит автоматически. */
const COMPANY_CREATE_STAGES = ["Дано обещание", "Ждет товар", "Товар в магазине"];

export function CompanyForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));
  const [dealId] = useState(() => crypto.randomUUID());
  const persistedRef = useRef<number | null>(null);
  const [persistedNumber, setPersistedNumber] = useState<number | null>(null);

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
  });

  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  const [companyName, setCompanyName] = useState("");
  const [managerName, setManagerName] = useState("");
  const [managerPhone, setManagerPhone] = useState("");
  const [issued, setIssued] = useState(false);
  const [atelier, setAtelier] = useState("");
  const [delivery, setDelivery] = useState("");

  const [items, setItems] = useState<CartItem[]>([]);

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items);
  const extras = (Number(atelier) || 0) + (Number(delivery) || 0);
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount) + extras;

  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const reserve = useAutoReserveUntil(paid, total);
  const change = Math.max(0, paid - total);
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });

  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Товар в магазине");

  const [actualUntil, setActualUntil] = useState(() => defaultHoldUntil(1));
  const [movementCreated, setMovementCreated] = useState(false);
  const fieldGaps = [
    stage === "Дано обещание" && !comment.trim() && "Комментарий к обещанию",
    stage === "Дано обещание" && !actualUntil && "Актуально до",
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    !isHoldStage(stage) && !managerPhone && "Телефон руководителя",
    !isHoldStage(stage) && !managerName && "Имя руководителя",
    !companyName.trim() && "Наименование компании",
    items.length === 0 && "Товары",
    ...changeTipsMissing(paid, total, changeInfo, tips),
  ].filter(Boolean) as string[];
  const missingRequired = [...fieldGaps, movementRequiredGap(stage, movementCreated)].filter(Boolean) as string[];
  const fieldsFilled = fieldGaps.length === 0;
  const baseFilled = missingRequired.length === 0;

  function rememberDeal(number: number) {
    persistedRef.current = number;
    setPersistedNumber(number);
  }

  function buildDeal(s: string): Deal {
    return {
      id: dealId,
      number: persistedRef.current ?? meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "company",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      clientName: client.name,
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel || undefined,
      purpose: client.purpose,
      companyName: companyName.trim() || undefined,
      managerName,
      managerPhone,
      // draft → Эдвину сразу задача «Выставить счет» (enqueueCompanyChain).
      invoiceStatus: "draft" as const,
      documentsStatus:
        s === "Документы переданы" ? "handed" : s === "Документы готовы" ? "ready" : "pending",
      atelierStatus: Number(atelier) > 0 ? "pending" : undefined,
      issued,
      atelierAmount: Number(atelier) || undefined,
      deliveryAmount: Number(delivery) || undefined,
      items,
      payments,
      stage: s,
      actualUntil: s === "Дано обещание" ? actualUntil : undefined,
      reservedUntil: reserve.forSave(s),
      comment,
      paymentDate: lastPaymentDate(payments),
      checkDiscount: discount || undefined,
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      total,
      paid,
    } as Deal;
  }

  function save(s: string) {
    if (persistedRef.current != null) {
      setSaved(true);
      setTimeout(onDone, 1100);
      return;
    }
    void addDeal(buildDeal(s)).then((savedDeal) => rememberDeal(savedDeal.number));
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  async function ensureDealForMove() {
    if (persistedRef.current != null) {
      return { dealNumber: persistedRef.current, store: activeStore };
    }
    if (!fieldsFilled) {
      throw new Error(`Сначала заполните: ${fieldGaps.join(", ")}`);
    }
    const savedDeal = await addDeal(buildDeal(stage), undefined, { strict: true });
    rememberDeal(savedDeal.number);
    setSaved(true);
    return { dealNumber: savedDeal.number, store: savedDeal.store || activeStore };
  }

  return (
    <FormShell
      onBack={onDone}
      title="Продажа компании"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      stageBadge={<StageBadge stage={stage} options={COMPANY_CREATE_STAGES} onChange={setStage} className="text-[16px] px-3.5 py-2" />}
      summary={<SummaryBar total={total} paid={paid} />}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={COMPANY_CREATE_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и контакт компании</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} nameLabel="ФИО клиента" />
        </div>
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <Field label="Телефон руководителя" required={!isHoldStage(stage)}>
            <input
              className="input"
              inputMode="tel"
              value={managerPhone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => setManagerPhone(formatPhone(e.target.value))}
            />
          </Field>
          <Field label="Имя руководителя" required={!isHoldStage(stage)}>
            <input
              className="input"
              value={managerName}
              onChange={(e) => setManagerName(e.target.value.replace(/[0-9]/g, ""))}
              placeholder="ФИО руководителя"
            />
          </Field>
          <Field label="Наименование компании" required>
            <input
              className="input"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="ООО «Вектор» · ИНН 7701234567"
            />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>{stage === "Дано обещание" ? "Интересующие товары" : "Товары, стоимость и скидка"}</SectionTitle>
        <ProductPicker
          items={items}
          onChange={setItems}
          dealNumber={persistedNumber ?? undefined}
          dealKind="company"
          ensureDeal={ensureDealForMove}
          onMovementCreated={() => {
            setMovementCreated(true);
            setSaved(true);
            setTimeout(onDone, 600);
          }}
        />
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <AtelierAmountField value={atelier} onChange={setAtelier} />
          <Field label="Доставка, ₽">
            <input className="input" inputMode="numeric" value={delivery} onChange={(e) => setDelivery(e.target.value.replace(/\D/g, ""))} placeholder="0" />
          </Field>
        </div>
        <div className="mt-4">
          <TotalsBlock
            subtotal={subtotal}
            state={disc}
            onChange={setDisc}
            itemsDiscount={cartItemsDiscount(items)}
            delivery={extras}
          />
        </div>
      </Card>

      <Card>
        <SectionTitle>Документы и выдача</SectionTitle>
        <p className="hint-only text-[12px] text-mute mb-3">
          После выполнения задачи «Сделать отложку» Эдвину уходит задача «Выставить счет». Номер и дату счёта
          он заполняет в своей очереди.
        </p>
        <label className={`rounded-lg border p-3 flex items-center gap-3 cursor-pointer ${issued ? "border-emerald-400/40 bg-emerald-400/10" : "border-ink-700"}`}>
          <input type="checkbox" checked={issued} onChange={(e) => setIssued(e.target.checked)} />
          <span className="text-white text-sm">Товар фактически выдан</span>
        </label>
      </Card>

      <Card>
        <SectionTitle>Оплата</SectionTitle>
        <PaymentSection
          total={total}
          payments={payments}
          onPayments={setPayments}
          consultant={consultants.consultant}
          changeInfo={changeInfo}
          onChangeInfo={setChangeInfo}
          tips={tips}
          onTips={setTips}
        />
      </Card>

      <Card>
        <SectionTitle>Источник и цель</SectionTitle>
        <SourceFields data={client} onChange={setClient} withPurpose={true} />
      </Card>

      <Card>
        <CommentField value={comment} onChange={setComment} required={stage === "Дано обещание"} />
      </Card>

      {stage === "Дано обещание" && <Card><Field label="Актуально до" required><DatePicker className="input" value={actualUntil} onChange={(value) => setActualUntil(value)} /></Field></Card>}
      {reserve.partial && stage !== "Успех" && (
        <Card>
          <Field label="Отложка до" hint="Частичная оплата — две недели вперёд, дату можно изменить">
            <DatePicker className="input" value={reserve.until} onChange={reserve.onChange} />
          </Field>
        </Card>
      )}
      {toast}
    </FormShell>
  );
}
