import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker } from "../../components/ProductPicker";
import {
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentSection,
  PhotoField,
  SectionTitle,
  SourceFields,
  StageActions,
  SummaryBar,
  TopUpBlock,
  TotalsBlock,
  calcDiscount,
  DateFieldHint,
  emptyTopUp,
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
  type TopUpInfo,
} from "./common";
import { RENTAL_SERVICE_PRICE, STORE_ADDRESS } from "../../data/mock";
import { money } from "../../lib/format";
import { attachmentsToUpload, type PhotoAttachment } from "../../lib/photo";
import type { CartItem, Deal, Payment } from "../../data/types";

// При создании — только оплата и «Ждет товар». «В аренде» / возврат — позже в карточке.
const SAVE_STAGES = ["Аренда оплачена", "Ждет товар"];

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

  // Комплект без цены — контроль движения товара
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
  const topUpChange = Math.max(0, topUpPaid - Math.max(0, remainder));
  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    !from && "Дата начала аренды",
    !to && "Дата конца аренды",
    items.length === 0 && "Комплект",
    photos.length === 0 && "Фото паспорта",
    stage === "Аренда оплачена" && totalPaid < total && "Полная оплата аренды",
    ...changeTipsMissing(paid, total, changeInfo, tips),
    ...changeTipsMissing(topUpPaid, Math.max(0, remainder), topUp.changeInfo, topUp.tips),
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    const rentalStatus =
      s === "Успех" ? "closed" :
      returnDate || s === "В аренде" ? "issued" :
      totalPaid >= total ? "paid" : "reserved";
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
        { productId: "rent", name: "Аренда комплекта", price: RENTAL_SERVICE_PRICE, qty: 1 },
        ...items,
      ],
      payments: [...payments, ...topUp.payments],
      stage: s,
      rentalFrom: from,
      rentalTo: to,
      issueDate: issueDate || undefined,
      returnDate: returnDate || undefined,
      rentalStatus,
      rentalIssuedAt: issueDate ? `${issueDate}T00:00:00.000Z` : undefined,
      rentalReturnedAt: returnDate ? `${returnDate}T00:00:00.000Z` : undefined,
      history: [
        { at: meta.createdAt, who: consultants.consultant, action: `Аренда запланирована: ${from} → ${to}` },
        ...(issueDate ? [{ at: `${issueDate}T00:00:00.000Z`, who: consultants.consultant, action: "Комплект фактически выдан" }] : []),
        ...(returnDate ? [{ at: `${returnDate}T00:00:00.000Z`, who: consultants.consultant, action: "Комплект фактически возвращён" }] : []),
      ],
      comment,
      paymentDate: lastPaymentDate([...payments, ...topUp.payments]),
      photoAttached: photos.length > 0,
      checkDiscount: discount || undefined,
      // Доплата закрывает остаток позже: сдача и чаевые берутся из того блока,
      // в котором они фактически возникли.
      ...tipsDealFields(topUp.tips.amount > 0 ? topUp.tips : tips, topUpChange > 0 ? topUpChange : change),
      ...changeDealFields(
        topUpChange > 0
          ? Math.max(0, topUpChange - (topUp.tips.amount || 0))
          : Math.max(0, change - (tips.amount || 0)),
        topUpChange > 0 ? topUp.changeInfo : changeInfo
      ),
      total,
      paid: totalPaid,
    }, attachmentsToUpload(photos));
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      onBack={onDone}
      title="Аренда"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={totalPaid} />}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={SAVE_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
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
          <span className="text-white text-sm">Аренда комплекта</span>
          <span className="text-gold-soft font-semibold">{money(RENTAL_SERVICE_PRICE)}</span>
        </div>
        <div className="field-label mb-2">Состав комплекта</div>
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
          <Field label="Фактическая дата выдачи">
            <input type="date" className="input" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />
            <DateFieldHint value={issueDate} />
          </Field>
          <Field label="Фактическая дата возврата">
            <input type="date" className="input" value={returnDate} onChange={(e) => setReturnDate(e.target.value)} />
            <DateFieldHint value={returnDate} />
          </Field>
        </div>
      </Card>

      {/* Оплата */}
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
        <div className="mt-4">
          <TopUpBlock
            remainder={remainder}
            info={topUp}
            onChange={setTopUp}
            consultant={consultants.consultant}
          />
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
