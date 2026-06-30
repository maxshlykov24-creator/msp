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
  TopUpBlock,
  TotalsBlock,
  calcDiscount,
  emptyTopUp,
  todayStr,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TopUpInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import type { CartItem, Deal, Payment } from "../../data/types";

const SAVE_STAGES = ["Ждет товар", "Товар в магазине", "Товар отложен"];

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
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
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
  const [reservedAt] = useState(todayStr());
  const [reservedUntil, setReservedUntil] = useState(addDays(todayStr(), 1));
  // Ручная правка срока — после неё авто-пересчёт не перезатирает значение.
  const [reservedManual, setReservedManual] = useState(false);

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items);
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount);

  const [payments, setPayments] = useState<Payment[]>([]);
  const [paymentDate, setPaymentDate] = useState(todayStr());
  const paid = payments.reduce((s, p) => s + p.amount, 0);

  // Срок отложки авто: без аванса — 1 день (бесплатно), с авансом — 2 недели.
  useEffect(() => {
    if (reservedManual) return;
    setReservedUntil(addDays(todayStr(), paid > 0 ? 14 : 1));
  }, [paid, reservedManual]);
  const remainder = total - paid;
  const change = Math.max(0, paid - total);

  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [topUp, setTopUp] = useState<TopUpInfo>(emptyTopUp);

  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Товар отложен");

  const topUpPaid = topUp.payments.reduce((s, p) => s + p.amount, 0);
  const totalPaid = paid + topUpPaid;
  const baseFilled = !!(client.name && client.phone && items.length > 0 && reservedUntil && client.purpose);
  const canSuccess = baseFilled && total - totalPaid <= 0;

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "deferred",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
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
      paymentDate,
      checkDiscount: discount || undefined,
      changeStatus: change > 0 ? changeInfo.status : undefined,
      changeDestination: change > 0 && changeInfo.status === "pending" ? changeInfo.destination : undefined,
      total,
      paid: totalPaid,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Отложка"
      subtitle="Оффлайн · резерв товара с авансом"
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
          successHint={!baseFilled ? "Заполните клиента, товары, срок и цель" : remainder > 0 && total - totalPaid > 0 ? `Остаток ${(total - totalPaid).toLocaleString("ru-RU")} ₽` : undefined}
          onSuccess={() => save("Успех")}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
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
          <TotalsBlock subtotal={subtotal} state={disc} onChange={setDisc} />
        </div>
      </Card>

      <Card>
        <SectionTitle>Оплата (аванс)</SectionTitle>
        <PaymentBlock total={total} payments={payments} onChange={setPayments} />
        <div className="mt-4">
          <PaymentDateField value={paymentDate} onChange={setPaymentDate} />
        </div>
        {change > 0 && (
          <div className="mt-4">
            <ChangeBlock change={change} info={changeInfo} onChange={setChangeInfo} />
          </div>
        )}
        <div className="mt-4">
          <TopUpBlock remainder={remainder} info={topUp} onChange={setTopUp} />
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
