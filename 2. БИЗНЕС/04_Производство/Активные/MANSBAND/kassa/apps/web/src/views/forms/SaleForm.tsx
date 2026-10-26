import { DatePicker } from "../../components/DatePicker";
import { defaultHoldUntil, isApproxDateStage } from "@kassa/shared";
import { useEffect, useRef, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field, StageBadge } from "../../components/ui";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
import { DealActionsBar } from "../../components/DealActionsBar";
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
import { STAGES_BY_KIND, STORE_ADDRESS } from "../../data/mock";
import type { CartItem, Deal, Payment } from "../../data/types";

// При создании: без «Провал» (это слив/не слив) и без «Товар в магазине» (проставляют позже).
const SAVE_STAGES = STAGES_BY_KIND.sale.filter((s) => !["Успех", "Товар в пути", "Товар отложен"].includes(s));

export function SaleForm({ onDone, onBack = onDone }: { onDone: () => void; onBack?: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber, findSlivByPhone } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));
  const [dealId] = useState(() => crypto.randomUUID());
  const persistedRef = useRef<number | null>(null);
  const [persistedNumber, setPersistedNumber] = useState<number | null>(null);

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
  const reserve = useAutoReserveUntil(paid, total);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  // Сдача → Эдвин
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });

  // Слив: подставляется автоматически по телефону (без отдельного блока)
  const [meetingDate, setMeetingDate] = useState("");
  const [approxMeetingDate, setApproxMeetingDate] = useState("");
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
  const [stage, setStage] = useState("Товар в магазине");

  const [actualUntil, setActualUntil] = useState(() => defaultHoldUntil(1));
  const [movementCreated, setMovementCreated] = useState(false);
  const fieldGaps = [
    stage === "Дано обещание" && !comment.trim() && "Комментарий к обещанию",
    stage === "Дано обещание" && !actualUntil && "Актуально до",
    isApproxDateStage(stage) && !approxMeetingDate && "Примерная дата встречи или заказа",
    stage === "Встреча назначена" && !meetingDate && "Дата встречи",
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    stage !== "Дано обещание" && items.length === 0 && "Товары",
    ...changeTipsMissing(paid, total, changeInfo, tips),
  ].filter(Boolean) as string[];
  const missingRequired = [...fieldGaps, movementRequiredGap(stage, movementCreated)].filter(Boolean) as string[];
  const fieldsFilled = fieldGaps.length === 0;
  const missingForSuccess = sourceMissingForSuccess(client);
  const baseFilled = missingRequired.length === 0;
  const canSuccess = items.length > 0 && fieldsFilled && missingForSuccess.length === 0 && remainder <= 0;

  function onFound(deal: Deal) {
    if (deal.referredBy) setConsultants((prev) => (prev.referredBy ? prev : { ...prev, referredBy: deal.referredBy! }));
    if (deal.meetingDate) setMeetingDate((prev) => prev || deal.meetingDate!);
  }

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
      kind: "sale",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      meetingDate: meetingDate || undefined,
      approxMeetingDate: isApproxDateStage(s) ? approxMeetingDate : undefined,
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
      actualUntil: s === "Дано обещание" ? actualUntil : undefined,
      reservedUntil: reserve.forSave(s),
      comment,
      paymentDate: lastPaymentDate(payments),
      linkedDealNumber: slivFound?.number,
      checkDiscount: discount || undefined,
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      total,
      paid,
    };
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
      onBack={onBack}
      title="Продажа"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      stageBadge={<StageBadge stage={stage} options={SAVE_STAGES} onChange={setStage} className="text-[16px] px-3.5 py-2 font-semibold shrink-0" />}
      headerActions={
        <DealActionsBar
          tasks={{
            dealNumber: persistedNumber ?? meta.number,
            store: activeStore,
            load: persistedNumber != null,
          }}
        />
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
            fieldsFilled && remainder <= 0 && missingForSuccess.length > 0
              ? `Для Успех: ${missingForSuccess.join(", ")}`
              : undefined
          }
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
            <Field label="Дата встречи" required={stage === "Встреча назначена"}>
              <DatePicker
                className="input"
                value={meetingDate}
                onChange={(value) => setMeetingDate(value)}
              />
            </Field>
          </div>
        )}
      </Card>

      {/* Товары, стоимость и скидка */}
      <Card>
        <SectionTitle>{stage === "Дано обещание" ? "Интересующие товары" : "Товары, стоимость и скидка"}</SectionTitle>
        <ProductPicker
          items={items}
          onChange={setItems}
          dealNumber={persistedNumber ?? undefined}
          dealKind="sale"
          ensureDeal={ensureDealForMove}
          onMovementCreated={() => {
            setMovementCreated(true);
            setSaved(true);
            setTimeout(onDone, 600);
          }}
        />
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
        <SourceFields
          data={client}
          onChange={setClient}
          checkTotal={total + saryBonus}
          items={items}
        />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} required={stage === "Дано обещание"} />
        </div>
      </Card>

      {stage === "Дано обещание" && <Card><Field label="Актуально до" required><DatePicker className="input" value={actualUntil} onChange={(value) => setActualUntil(value)} /></Field></Card>}
      {reserve.partial && stage !== "Успех" && (
        <Card>
          <Field label="Отложка до" hint="Частичная оплата — две недели вперёд, дату можно изменить">
            <DatePicker className="input" value={reserve.until} onChange={reserve.onChange} />
          </Field>
        </Card>
      )}
      {isApproxDateStage(stage) && <Card><Field label="Примерная дата встречи или заказа" required><DatePicker className="input" value={approxMeetingDate} onChange={(value) => setApproxMeetingDate(value)} /></Field></Card>}
      {stage === "Встреча назначена" && !slivFound && <Card><Field label="Дата встречи" required><DatePicker className="input" value={meetingDate} onChange={(value) => setMeetingDate(value)} /></Field></Card>}
      {toast}
    </FormShell>
  );
}
