import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { PaymentBlock } from "../../components/PaymentBlock";
import {
  ChangeBlock,
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentDateField,
  SectionTitle,
  SourceFields,
  StageActions,
  SummaryBar,
  TotalsBlock,
  calcDiscount,
  todayStr,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone } from "../../lib/format";
import type { CartItem, Payment } from "../../data/types";

const SENDER = "MANSBAND · г. Москва, Спартаковская пл., д. 14, стр. 2 · +7 (495) 000-00-00";

export function DeliveryForm({ onDone }: { onDone: () => void }) {
  const { activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const store = "Онлайн-магазин" as const;

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
    callManager: "",
  });

  const [city, setCity] = useState("");
  const [senderOpen, setSenderOpen] = useState(false);

  // Получатель
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");

  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items) + (Number(delivery) || 0);
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount);

  const [payments, setPayments] = useState<Payment[]>([]);
  const [paymentDate, setPaymentDate] = useState(todayStr());
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const change = Math.max(0, paid - total);
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });

  const [comment, setComment] = useState("");

  const baseFilled = !!(
    consultants.callManager &&
    city &&
    recipientName &&
    recipientPhone &&
    items.length > 0 &&
    client.channel &&
    client.purpose
  );

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "online",
      kind: "delivery",
      consultant: consultants.consultant,
      callManager: consultants.callManager || undefined,
      clientName: recipientName,
      clientPhone: recipientPhone,
      store,
      storeAddress: STORE_ADDRESS[store],
      deliveryCity: city,
      recipientName,
      recipientPhone,
      channel: client.channel,
      purpose: client.purpose,
      items,
      payments,
      stage: s,
      comment,
      paymentDate,
      checkDiscount: discount || undefined,
      changeStatus: change > 0 ? changeInfo.status : undefined,
      changeDestination: change > 0 && changeInfo.status === "pending" ? changeInfo.destination : undefined,
      total,
      paid,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Доставка (СДЭК)"
      subtitle="Онлайн · отправка заказа курьерской службой"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[store]}
      summary={<SummaryBar total={total} paid={paid} />}
      footer={
        <StageActions
          stages={["Передан на сборку"]}
          stage="Передан на сборку"
          onStageChange={() => {}}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successHint={!baseFilled ? "Заполните call-менеджера, город, получателя, товары, источник и цель" : "Этап «Передан на сборку» — авто"}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и call-менеджер</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} withCallManager />
        <div className="mt-4">
          <Field label="Тип заказа">
            <input className="input opacity-70" readOnly value="Доставка СДЭК" />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>Получатель и адрес</SectionTitle>
        <Field label="Город получателя" required>
          <input className="input" value={city} onChange={(e) => setCity(e.target.value)} placeholder="Москва, Санкт-Петербург…" />
        </Field>

        <button
          type="button"
          onClick={() => setSenderOpen((v) => !v)}
          className="mt-4 inline-flex items-center gap-1.5 text-sm text-mute hover:text-white"
        >
          {senderOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />} Отправитель (авто)
        </button>
        {senderOpen && (
          <div className="mt-2 text-[13px] text-mute bg-ink-900 border border-ink-700 rounded-lg p-3">{SENDER}</div>
        )}

        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="ФИО получателя" required>
            <input
              className="input"
              value={recipientName}
              onChange={(e) => setRecipientName(e.target.value.replace(/[0-9]/g, ""))}
              placeholder="Кому доставить"
            />
          </Field>
          <Field label="Телефон получателя" required>
            <input
              className="input"
              inputMode="tel"
              value={recipientPhone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => setRecipientPhone(formatPhone(e.target.value))}
            />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
        <div className="mt-3 max-w-[220px]">
          <div className="field-label">Доставка (₽)</div>
          <input
            className="input"
            inputMode="numeric"
            placeholder="0"
            value={delivery}
            onChange={(e) => setDelivery(e.target.value.replace(/[^\d]/g, ""))}
          />
        </div>
        <div className="mt-4">
          <TotalsBlock subtotal={subtotal} state={disc} onChange={setDisc} />
        </div>
      </Card>

      <Card>
        <SectionTitle>Оплата</SectionTitle>
        <PaymentBlock total={total} payments={payments} onChange={setPayments} />
        <div className="mt-4">
          <PaymentDateField value={paymentDate} onChange={setPaymentDate} />
        </div>
        {change > 0 && (
          <div className="mt-4">
            <ChangeBlock change={change} info={changeInfo} onChange={setChangeInfo} />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Источник и цель</SectionTitle>
        <SourceFields data={client} onChange={setClient} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} />
        </div>
      </Card>

      {toast}
    </FormShell>
  );
}
