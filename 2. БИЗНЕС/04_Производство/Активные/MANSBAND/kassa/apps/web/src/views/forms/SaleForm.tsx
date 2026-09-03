import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field, Modal, StageBadge } from "../../components/ui";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
import { DealActionsBar } from "../../components/DealActionsBar";
import { CreateTaskForm } from "../../components/CreateTaskForm";
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
  sourceMissingForSuccess,
  tipsDealFields,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
} from "./common";
import { STAGES_BY_KIND, STORE_ADDRESS } from "../../data/mock";
import type { CartItem, Deal, Payment } from "../../data/types";

// При создании: без «Провал» (это слив/не слив) и без «Товар в магазине» (проставляют позже).
const SAVE_STAGES = STAGES_BY_KIND.sale.filter((s) => s !== "Успех" && s !== "Товар в магазине");

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
  const subtotal = cartTotal(items);
  const deliveryAmount = Number(delivery) || 0;
  const discount = calcDiscount(subtotal, disc);
  // Бонус сарафана вычитается из чека отдельно от скидки (правки 10.08, п.3).
  const saryBonus = client.saryBonus ?? 0;
  const total = Math.max(0, Math.max(0, subtotal - discount) + deliveryAmount - saryBonus);

  // Оплата
  const [payments, setPayments] = useState<Payment[]>([]);
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  // Сдача → Эдвин
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });

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
  const [taskOpen, setTaskOpen] = useState(false);

  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    items.length === 0 && "Товары",
    ...changeTipsMissing(paid, total, changeInfo, tips),
  ].filter(Boolean) as string[];
  const missingForSuccess = sourceMissingForSuccess(client);
  const baseFilled = missingRequired.length === 0;
  const canSuccess = baseFilled && missingForSuccess.length === 0 && remainder <= 0;

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
      saryPhone: client.saryPhone || undefined,
      saryClient: client.saryClient || undefined,
      saryBonus: saryBonus || undefined,
      items,
      payments,
      stage: s,
      comment,
      paymentDate: lastPaymentDate(payments),
      linkedDealNumber: slivFound?.number,
      checkDiscount: discount || undefined,
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      total,
      paid,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      onBack={onDone}
      title="Продажа"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      stageBadge={<StageBadge stage={stage} className="text-[14px] px-3 py-1.5 font-semibold shrink-0" />}
      headerActions={
        <DealActionsBar onTask={() => setTaskOpen(true)} />
      }
      summary={<SummaryBar total={total} paid={paid} />}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={SAVE_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          successStage="Успех"
          successDisabled={!canSuccess}
          successHint={
            baseFilled && remainder <= 0 && missingForSuccess.length > 0
              ? `Для Успех: ${missingForSuccess.join(", ")}`
              : undefined
          }
          onSuccess={() => save("Успех")}
        />
      }
    >
      <Modal open={taskOpen} onClose={() => setTaskOpen(false)} title="Создать задачу">
        <CreateTaskForm
          defaultStore={activeStore}
          dealNumber={meta.number}
          lockDealNumber
          simple
          compact
          onCreated={() => setTaskOpen(false)}
        />
      </Modal>
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
            <Field label="Дата встречи">
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
        <ProductPicker items={items} onChange={setItems} dealNumber={meta.number} />
        <div className="mt-3 max-w-[220px]">
          <div className="field-label">Доставка</div>
          <input
            className="input"
            inputMode="numeric"
            placeholder="0"
            value={delivery}
            onChange={(e) => setDelivery(e.target.value.replace(/[^\d]/g, ""))}
          />
        </div>
        <div className="mt-4">
          <TotalsBlock
            subtotal={subtotal}
            state={disc}
            onChange={setDisc}
            itemsDiscount={cartItemsDiscount(items)}
            delivery={deliveryAmount}
            saryBonus={saryBonus}
          />
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
      </Card>

      {/* Источник и цель + комментарий */}
      <Card>
        <SectionTitle>Источник и цель</SectionTitle>
        {/* Порог САР считаем от чека до вычета бонуса: сам бонус порог не ломает. */}
        <SourceFields data={client} onChange={setClient} checkTotal={total + saryBonus} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} />
        </div>
      </Card>

      {toast}
    </FormShell>
  );
}
