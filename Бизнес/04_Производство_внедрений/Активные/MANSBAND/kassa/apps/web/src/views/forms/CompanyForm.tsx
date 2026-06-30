import { useState } from "react";
import { Building2 } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { PaymentBlock } from "../../components/PaymentBlock";
import {
  ClientFields,
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
  useSaved,
  type ClientData,
  type ConsultantData,
  type DiscountState,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone } from "../../lib/format";
import type { CartItem, Payment } from "../../data/types";

// Этапы продажи компании (правки 7)
const COMPANY_STAGES = ["Ждёт товар", "Товар в магазине", "Ждёт оплату", "Товар отложен", "Провал"];

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function addMonth(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    d.setMonth(d.getMonth() + 1);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function CompanyForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  // 5–6. Консультанты
  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
  });

  // 7–8. Контактное лицо (ФИО + телефон)
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  // 9. Имя руководителя (обяз.)
  const [managerName, setManagerName] = useState("");

  // 10. Телефон руководителя (обяз., маска)
  const [managerPhone, setManagerPhone] = useState("");

  // 11. Название компании (обяз.)
  const [companyName, setCompanyName] = useState("");

  // 11. Отложенные товары
  const [items, setItems] = useState<CartItem[]>([]);

  // 12. Дата отложки (авто = сегодня)
  const [deferredAt] = useState(todayStr());

  // 13. Срок отложки (авто +1 мес, редактируемый, обяз.)
  const [deferredUntil, setDeferredUntil] = useState(addMonth(todayStr()));

  // 14. Сумма + скидка
  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items);
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount);

  // 15. Оплата
  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);

  // Дата оплаты (авто = сегодня)
  const [paymentDate, setPaymentDate] = useState(todayStr());

  // 19. Комментарий (не обязателен)
  const [comment, setComment] = useState("");

  // 20. Этап
  const [stage, setStage] = useState("Товар отложен");

  const baseFilled = !!(
    client.name &&
    client.phone &&
    managerName &&
    managerPhone &&
    companyName &&
    items.length > 0 &&
    deferredUntil &&
    client.purpose
  );

  function save(s: string) {
    addDeal({
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
      companyName,
      managerName,
      managerPhone,
      deferredUntil,
      items,
      payments,
      stage: s,
      comment,
      paymentDate,
      checkDiscount: discount || undefined,
      total,
      paid,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Продажа компании"
      subtitle="Оффлайн · юрлицо, отложка + счёт"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={paid} />}
      footer={
        <StageActions
          stages={COMPANY_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successStage="Успех"
          successDisabled={!baseFilled || paid < total}
          successHint={baseFilled && paid < total ? `Остаток ${(total - paid).toLocaleString("ru-RU")} ₽ — Успех недоступен` : undefined}
          onSuccess={() => save("Успех")}
        />
      }
    >
      {/* Консультант + клиент + руководитель + компания */}
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
        </div>
        <div className="mt-4">
          <Field label="Название компании" required>
            <div className="relative">
              <Building2 size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
              <input
                className="input pl-9"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="ООО «…»"
              />
            </div>
          </Field>
        </div>
      </Card>

      {/* Товары, стоимость и скидка + сроки отложки */}
      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="Дата отложки">
            <input type="date" className="input opacity-70" readOnly value={deferredAt} />
          </Field>
          <Field label="Срок отложки до" required>
            <input
              type="date"
              className="input"
              value={deferredUntil}
              onChange={(e) => setDeferredUntil(e.target.value)}
            />
          </Field>
        </div>
        <div className="mt-4">
          <TotalsBlock subtotal={subtotal} state={disc} onChange={setDisc} />
        </div>
      </Card>

      {/* Оплата */}
      <Card>
        <SectionTitle>Оплата</SectionTitle>
        <PaymentBlock total={total} payments={payments} onChange={setPayments} />
        <div className="mt-4">
          <PaymentDateField value={paymentDate} onChange={setPaymentDate} />
        </div>
      </Card>

      {/* Источник рекламы + цель */}
      <Card>
        <SectionTitle>Источник и цель</SectionTitle>
        <SourceFields data={client} onChange={setClient} withPurpose={true} />
      </Card>

      {/* 19. Комментарий */}
      <Card>
        <CommentField value={comment} onChange={setComment} />
      </Card>

      {toast}
    </FormShell>
  );
}
