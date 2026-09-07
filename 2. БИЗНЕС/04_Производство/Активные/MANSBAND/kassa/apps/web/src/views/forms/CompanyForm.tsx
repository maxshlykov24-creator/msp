import { useState } from "react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
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
  tipsDealFields,
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
const COMPANY_CREATE_STAGES = ["Товар отложен"];

export function CompanyForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

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
  const change = Math.max(0, paid - total);
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });

  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Товар отложен");

  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    !managerPhone && "Телефон руководителя",
    !managerName && "Имя руководителя",
    !companyName.trim() && "Наименование компании",
    items.length === 0 && "Товары",
    ...changeTipsMissing(paid, total, changeInfo, tips),
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    const deal = {
      id: crypto.randomUUID(),
      number: meta.number,
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
      comment,
      paymentDate: lastPaymentDate(payments),
      checkDiscount: discount || undefined,
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      total,
      paid,
    } as Deal;
    addDeal(deal);
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      onBack={onDone}
      title="Продажа компании"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
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
          <Field label="Телефон руководителя" required>
            <input
              className="input"
              inputMode="tel"
              value={managerPhone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => setManagerPhone(formatPhone(e.target.value))}
            />
          </Field>
          <Field label="Имя руководителя" required>
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
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <Field label="Ателье, ₽">
            <input className="input" inputMode="numeric" value={atelier} onChange={(e) => setAtelier(e.target.value.replace(/\D/g, ""))} placeholder="0" />
          </Field>
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
        <p className="text-[12px] text-mute mb-3">
          При сохранении Эдвину автоматически уходит задача «Выставить счет». Номер и дату счёта
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
        <CommentField value={comment} onChange={setComment} />
      </Card>

      {toast}
    </FormShell>
  );
}
