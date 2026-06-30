import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { PaymentBlock } from "../../components/PaymentBlock";
import {
  ChangeBlock,
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  PaymentDateField,
  SectionTitle,
  SourceFields,
  StageActions,
  SummaryBar,
  TipsBlock,
  TotalsBlock,
  calcDiscount,
  todayStr,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
} from "./common";
import { SALE_STAGES, STORE_ADDRESS } from "../../data/mock";
import type { CartItem, Deal, Payment } from "../../data/types";


// Этапы для ручного сохранения (без «Успех» — он отдельной кнопкой)
const SAVE_STAGES = SALE_STAGES.filter((s) => s !== "Успех" && s !== "Провал");

export function SaleForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber, findSlivByPhone } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  // Консультанты (5–6)
  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
  });

  // Клиент (7–8)
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  // Позиции
  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");

  // Скидка на весь чек
  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items) + (Number(delivery) || 0);
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount);

  // Оплата
  const [payments, setPayments] = useState<Payment[]>([]);
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  // Сдача → Эдвин
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });

  // Дата оплаты (авто = сегодня)
  const [paymentDate, setPaymentDate] = useState(todayStr());

  // Слив: подставляется автоматически по телефону (без отдельного блока)
  const [meetingDate, setMeetingDate] = useState("");
  const [slivFound, setSlivFound] = useState<Deal | null>(null);

  // Авто-проверка слива при полном вводе телефона (11 цифр).
  // Подставляем направившего/дату встречи только при НОВОЙ находке и если поле пустое —
  // авто-подстановка не перезаписывает ручные правки.
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
      if (!meetingDate) {
        if (found.meetingDate) setMeetingDate(found.meetingDate);
        else if (found.createdAt) setMeetingDate(found.createdAt.slice(0, 10));
      }
    } else if (!found) {
      setSlivFound(null);
    }
  }, [client.phone]);

  // Комментарий (не обязателен)
  const [comment, setComment] = useState("");

  // Этап для ручного сохранения
  const [stage, setStage] = useState("Товар отложен");

  const baseFilled = !!(client.name && client.phone && items.length > 0);
  const canSuccess = baseFilled && remainder <= 0;

  function onFound(deal: Deal) {
    if (deal.referredBy) setConsultants((prev) => (prev.referredBy ? prev : { ...prev, referredBy: deal.referredBy! }));
    if (deal.meetingDate) setMeetingDate((prev) => prev || deal.meetingDate!);
  }

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "sale",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      meetingDate: meetingDate || undefined,
      clientName: client.name,
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel,
      purpose: client.purpose,
      items,
      payments,
      stage: s,
      comment,
      paymentDate,
      linkedDealNumber: slivFound?.number,
      checkDiscount: discount || undefined,
      tips: tips.amount || undefined,
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
      title="Продажа"
      subtitle="Оффлайн · магазин"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={paid} />}
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
          successHint={!baseFilled ? "Заполните телефон, имя и товары" : undefined}
          onSuccess={() => save("Успех")}
        />
      }
    >
      {/* Консультант + клиент (объединённый блок) */}
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} onFound={onFound} />
        </div>
        {slivFound && (
          <div className="mt-3 flex items-center gap-2 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-3 py-2">
            <UserCheck size={13} />
            Пришёл по сливу: заявка #{slivFound.number} · направил {slivFound.consultant}
            {meetingDate && ` · встреча ${meetingDate}`}
          </div>
        )}
        {slivFound && (
          <div className="mt-3 max-w-[220px]">
            <Field label="Дата встречи (из слива)">
              <input
                type="date"
                className="input"
                value={meetingDate}
                onChange={(e) => setMeetingDate(e.target.value)}
              />
            </Field>
          </div>
        )}
      </Card>

      {/* Товары, стоимость и скидка */}
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
      </Card>

      {/* Источник и цель + комментарий */}
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
