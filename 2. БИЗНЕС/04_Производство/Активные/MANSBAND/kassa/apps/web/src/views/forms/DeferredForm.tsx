import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
import { DealActionsBar } from "../../components/DealActionsBar";
import { MovementModal, defaultMovementTarget } from "../../components/MovementModal";
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
  TopUpBlock,
  TotalsBlock,
  calcDiscount,
  changeDealFields,
  changeTipsMissing,
  emptyTopUp,
  lastPaymentDate,
  sourceMissingForSuccess,
  tipsDealFields,
  todayStr,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
  type TopUpInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { useAuth } from "../../auth/AuthContext";
import type { CartItem, Deal, Payment } from "../../data/types";

/**
 * При создании без перемещения — «Товар в магазине» → задача «Сделать отложку».
 * «Товар отложен» ставит только завершение этой задачи, не селект создания.
 */
const SAVE_STAGES = ["Ждет товар", "Товар в магазине"];

/** Дата + n дней от YYYY-MM-DD. */
function addDays(dateStr: string, n: number): string {
  try {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function DeferredForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber, findSlivByPhone } = useStore();
  const { user } = useAuth();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));
  const [dealId] = useState(() => crypto.randomUUID());

  // Отложка от колл-менеджера (созвон 20.08): консультант пуст, авторство — при продаже.
  const isCallManager = user?.role === "crm";
  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: isCallManager ? "" : activeConsultant,
    referredBy: "",
    callManager: isCallManager ? user?.name : undefined,
  });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  // Авто-слив
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
    } else if (!found) {
      setSlivFound(null);
    }
  }, [client.phone]);

  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");
  const [reservedAt] = useState(todayStr());
  const [reservedUntil, setReservedUntil] = useState(addDays(todayStr(), 1));
  // Ручная правка срока — после неё авто-пересчёт не перезатирает значение.
  const [reservedManual, setReservedManual] = useState(false);

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items);
  const discount = calcDiscount(subtotal, disc);
  const deliveryAmount = Number(delivery) || 0;
  const total = Math.max(0, subtotal - discount) + deliveryAmount;

  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);

  // Срок отложки авто: без аванса — 1 день (бесплатно), с авансом — 2 недели.
  useEffect(() => {
    if (reservedManual) return;
    setReservedUntil(addDays(todayStr(), paid > 0 ? 14 : 1));
  }, [paid, reservedManual]);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const [topUp, setTopUp] = useState<TopUpInfo>(emptyTopUp);

  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Товар в магазине");
  const [moveOpen, setMoveOpen] = useState(false);
  const [persistedNumber, setPersistedNumber] = useState<number | null>(null);

  const topUpPaid = topUp.payments.reduce((s, p) => s + p.amount, 0);
  const totalPaid = paid + topUpPaid;
  const topUpChange = Math.max(0, topUpPaid - Math.max(0, remainder));
  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    items.length === 0 && "Товары",
    !reservedUntil && "Срок действия отложки",
    ...changeTipsMissing(paid, total, changeInfo, tips),
    ...changeTipsMissing(topUpPaid, Math.max(0, remainder), topUp.changeInfo, topUp.tips),
  ].filter(Boolean) as string[];
  const missingForSuccess = sourceMissingForSuccess(client);
  const baseFilled = missingRequired.length === 0;
  const canSuccess = baseFilled && missingForSuccess.length === 0 && total - totalPaid <= 0;

  function buildDeal(s: string): Deal {
    return {
      id: dealId,
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "deferred",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      callManager: consultants.callManager || undefined,
      clientName: client.name,
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel,
      purpose: client.purpose,
      items,
      payments: [...payments, ...topUp.payments],
      stage: s,
      reservedUntil,
      comment,
      paymentDate: lastPaymentDate([...payments, ...topUp.payments]),
      checkDiscount: discount || undefined,
      deliveryAmount: deliveryAmount || undefined,
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
    };
  }

  async function save(s: string) {
    const savedDeal = await addDeal(buildDeal(s));
    setPersistedNumber(savedDeal.number);
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  async function ensureDealForMove() {
    if (!baseFilled) return null;
    if (persistedNumber != null) {
      return { dealNumber: persistedNumber, store: activeStore };
    }
    const savedDeal = await addDeal(buildDeal("Ждет товар"));
    setPersistedNumber(savedDeal.number);
    setStage("Ждет товар");
    setSaved(true);
    return { dealNumber: savedDeal.number, store: savedDeal.store || activeStore };
  }

  return (
    <FormShell
      onBack={onDone}
      title="Отложка"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={totalPaid} />}
      missingRequired={missingRequired}
      headerActions={
        <DealActionsBar onMovement={items.length > 0 ? () => setMoveOpen(true) : undefined} />
      }
      footer={
        <StageActions
          stages={SAVE_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={(s) => void save(s)}
          saved={saved}
          disabled={!baseFilled}
          successStage="Успех"
          successDisabled={!canSuccess}
          successHint={
            baseFilled && total - totalPaid > 0
              ? `Остаток ${(total - totalPaid).toLocaleString("ru-RU")} ₽`
              : baseFilled && missingForSuccess.length > 0
                ? `Для Успех: ${missingForSuccess.join(", ")}`
                : undefined
          }
          onSuccess={() => void save("Успех")}
        />
      }
    >
      <MovementModal
        open={moveOpen}
        onClose={() => setMoveOpen(false)}
        dealNumber={persistedNumber ?? undefined}
        store={activeStore}
        items={items}
        defaultTarget={defaultMovementTarget(activeStore)}
        ensureDeal={persistedNumber == null ? () => ensureDealForMove() : undefined}
        onCreated={() => {
          setStage("Ждет товар");
          setSaved(true);
          setTimeout(onDone, 600);
        }}
      />

      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields
          data={consultants}
          onChange={setConsultants}
          allowEmptyConsultant={isCallManager}
        />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        {slivFound && (
          <div className="mt-3 flex items-center gap-2 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-3 py-2">
            <UserCheck size={13} /> Пришёл по сливу: заявка #{slivFound.number} · направил {slivFound.consultant}
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
        <div className="mt-3 max-w-[220px]">
          <Field label="Доставка, ₽">
            <input className="input" inputMode="numeric" value={delivery} onChange={(e) => setDelivery(e.target.value.replace(/\D/g, ""))} placeholder="0" />
          </Field>
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="Дата отложки">
            <input type="date" className="input opacity-70" readOnly value={reservedAt} />
          </Field>
          <Field label="Срок действия отложки" required hint={paid > 0 ? "С авансом — 2 недели (можно изменить)" : "Без аванса — 1 день (можно изменить)"}>
            <input
              type="date"
              className="input"
              value={reservedUntil}
              onChange={(e) => { setReservedUntil(e.target.value); setReservedManual(true); }}
            />
          </Field>
        </div>
        <div className="mt-4">
          <TotalsBlock
            subtotal={subtotal}
            state={disc}
            onChange={setDisc}
            itemsDiscount={cartItemsDiscount(items)}
            delivery={deliveryAmount}
          />
        </div>
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
        <div className="mt-4">
          <TopUpBlock
            remainder={remainder}
            info={topUp}
            onChange={setTopUp}
            consultant={consultants.consultant}
          />
        </div>
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
