import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker } from "../../components/ProductPicker";
import { PaymentBlock } from "../../components/PaymentBlock";
import {
  ChangeBlock,
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentDateField,
  PhotoField,
  SectionTitle,
  SourceFields,
  StageActions,
  SummaryBar,
  TipsBlock,
  TopUpBlock,
  TotalsBlock,
  calcDiscount,
  DateFieldHint,
  emptyTopUp,
  todayStr,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
  type TopUpInfo,
} from "./common";
import { RENTAL_SERVICE_PRICE, STORE_ADDRESS } from "../../data/mock";
import { money } from "../../lib/format";
import { attachmentsToUpload, type PhotoAttachment } from "../../lib/photo";
import type { CartItem, Deal, Payment } from "../../data/types";

const SAVE_STAGES = ["Аренда оплачена", "В аренде"];

/** Прибавить дни к дате YYYY-MM-DD. */
function addDays(dateStr: string, days: number): string {
  try {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function RentalForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber, findSlivByPhone } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  // Консультант + клиент
  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  // Авто-слив по телефону
  const [meetingDate, setMeetingDate] = useState("");
  const [slivFound, setSlivFound] = useState<Deal | null>(null);
  useEffect(() => {
    const digits = client.phone.replace(/\D/g, "");
    if (digits.length !== 11) {
      setSlivFound(null);
      return;
    }
    const found = findSlivByPhone(client.phone);
    if (found && found.id !== slivFound?.id) {
      setSlivFound(found);
      setConsultants((prev) => (prev.referredBy ? prev : { ...prev, referredBy: found.consultant }));
      if (!meetingDate && found.meetingDate) setMeetingDate(found.meetingDate);
    } else if (!found) {
      setSlivFound(null);
    }
  }, [client.phone]);

  // Комплект (без цены — для контроля движения товара)
  const [items, setItems] = useState<CartItem[]>([]);

  // Сроки аренды: начало (план) → конец = авто (+2 дня, редактируемый).
  // Выдача / возврат (факт) — без дефолта.
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [issueDate, setIssueDate] = useState("");
  const [returnDate, setReturnDate] = useState("");

  function handleFrom(val: string) {
    setFrom(val);
    setTo(val ? addDays(val, 2) : "");
  }

  // Скидка + оплата
  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = RENTAL_SERVICE_PRICE;
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount);

  const [payments, setPayments] = useState<Payment[]>([]);
  const [paymentDate, setPaymentDate] = useState(todayStr());
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const [topUp, setTopUp] = useState<TopUpInfo>(emptyTopUp);

  // Фото паспорта (обяз.)
  const [photos, setPhotos] = useState<PhotoAttachment[]>([]);

  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Аренда оплачена");

  const topUpPaid = topUp.payments.reduce((s, p) => s + p.amount, 0);
  const totalPaid = paid + topUpPaid;
  const baseFilled = !!(client.name && client.phone && from && to && items.length > 0 && photos.length > 0);
  const canSuccess = baseFilled && total - totalPaid <= 0;

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "rental",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      meetingDate: meetingDate || undefined,
      clientName: client.name,
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel,
      purpose: client.purpose,
      items: [
        { productId: "rent", name: "Аренда комплекта (услуга)", price: RENTAL_SERVICE_PRICE, qty: 1 },
        ...items,
      ],
      payments: [...payments, ...topUp.payments],
      stage: s,
      rentalFrom: from,
      rentalTo: to,
      issueDate: issueDate || undefined,
      returnDate: returnDate || undefined,
      comment,
      paymentDate,
      photoAttached: photos.length > 0,
      checkDiscount: discount || undefined,
      tips: tips.amount || undefined,
      tipsDestination: tips.amount > 0 && tips.status === "pending" ? tips.destination || undefined : undefined,
      changeStatus: change > 0 ? changeInfo.status : undefined,
      changeDestination: change > 0 && changeInfo.status === "pending" ? changeInfo.destination : undefined,
      total,
      paid: totalPaid,
    }, attachmentsToUpload(photos));
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Аренда"
      subtitle="Оффлайн · услуга 6 500 ₽, костюм возвращается"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={totalPaid} />}
      footer={
        <StageActions
          stages={SAVE_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successStage="Успех"
          successDisabled={!canSuccess}
          successHint={photos.length === 0 ? "Нужно фото паспорта" : !baseFilled ? "Заполните клиента, даты и комплект" : undefined}
          onSuccess={() => save("Успех")}
        />
      }
    >
      {/* Консультант + клиент */}
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        {slivFound && (
          <div className="mt-3 flex items-center gap-2 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-3 py-2">
            <UserCheck size={13} />
            Пришёл по сливу: заявка #{slivFound.number} · направил {slivFound.consultant}
          </div>
        )}
      </Card>

      {/* Товары, стоимость и скидка */}
      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <div className="flex items-center justify-between rounded-lg bg-ink-900 border border-ink-700 px-4 py-3 mb-3">
          <span className="text-white text-sm">Аренда комплекта (услуга)</span>
          <span className="text-gold-soft font-semibold">{money(RENTAL_SERVICE_PRICE)}</span>
        </div>
        <div className="field-label mb-2">Состав комплекта (для контроля движения)</div>
        <ProductPicker items={items} onChange={setItems} noPrice />
        <div className="mt-4">
          <TotalsBlock subtotal={subtotal} state={disc} onChange={setDisc} />
        </div>
      </Card>

      {/* Сроки аренды + выдача/возврат */}
      <Card>
        <SectionTitle>Сроки</SectionTitle>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="Дата начала аренды" required>
            <input type="date" className="input" value={from} onChange={(e) => handleFrom(e.target.value)} />
            <DateFieldHint value={from} />
          </Field>
          <Field label="Дата конца аренды" required>
            <input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
            <DateFieldHint value={to} />
          </Field>
          <Field label="Дата выдачи (факт)">
            <input type="date" className="input" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />
            <DateFieldHint value={issueDate} />
          </Field>
          <Field label="Дата возврата (факт)">
            <input type="date" className="input" value={returnDate} onChange={(e) => setReturnDate(e.target.value)} />
            <DateFieldHint value={returnDate} />
          </Field>
        </div>
      </Card>

      {/* Оплата */}
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
        {change > 0 && (
          <div className="mt-3">
            <TipsBlock change={change} info={tips} onChange={setTips} responsible={consultants.consultant} />
          </div>
        )}
        <div className="mt-4">
          <TopUpBlock remainder={remainder} info={topUp} onChange={setTopUp} />
        </div>
      </Card>

      {/* Источник + цель + комментарий */}
      <Card>
        <SectionTitle>Источник и цель</SectionTitle>
        <SourceFields data={client} onChange={setClient} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} />
        </div>
      </Card>

      {/* Фото паспорта / прав (залог) */}
      <Card>
        <SectionTitle>Фото паспорта / прав</SectionTitle>
        <PhotoField photos={photos} onChange={setPhotos} label="Фото паспорта / прав" />
      </Card>

      {toast}
    </FormShell>
  );
}
