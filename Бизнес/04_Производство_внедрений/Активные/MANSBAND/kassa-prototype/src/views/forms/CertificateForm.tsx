import { useState } from "react";
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
  PhotoField,
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
import { STORE_ADDRESS } from "../../data/mock";
import { money } from "../../lib/format";
import type { CartItem, Payment } from "../../data/types";

const NOMINALS = [5000, 10000, 15000, 20000, 25000, 30000];
const CERT_STAGES = ["Сертификат продан", "Успех", "Провал"];

function addYear(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    d.setFullYear(d.getFullYear() + 1);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function CertificateForm({ onDone, digital = false }: { onDone: () => void; digital?: boolean }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  // 5–6. Консультанты (+ call-менеджер для электронного)
  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
    callManager: "",
  });

  // 7–8. Клиент
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  // E-mail для отправки (электронный)
  const [email, setEmail] = useState("");

  // 9. Гость
  const [guest, setGuest] = useState("");

  // 10. Позиции (номинал = qty 1 через кнопки) + доставка
  const [useNominal, setUseNominal] = useState(true);
  const [nominal, setNominal] = useState<number>(15000);
  const [custom, setCustom] = useState("");
  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");

  // 11. Номер сертификата
  const [certNumber, setCertNumber] = useState("");

  // 12. Дата получения (авто = сегодня, редактируемая)
  const [receivedAt, setReceivedAt] = useState(todayStr());

  // 13. Действителен до (авто +1 год, редактируемая)
  const [validUntil, setValidUntil] = useState(addYear(todayStr()));

  // Синхронизация validUntil при изменении receivedAt
  function handleReceivedAt(val: string) {
    setReceivedAt(val);
    setValidUntil(addYear(val));
  }

  // 14. Скидка
  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });

  // Итог до скидки
  const nominalTotal = useNominal
    ? (custom ? Number(custom) : nominal)
    : cartTotal(items) + (Number(delivery) || 0);
  const discount = calcDiscount(nominalTotal, disc);
  const total = Math.max(0, nominalTotal - discount);

  // 15. Оплата
  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);

  // 17. Сдача
  const change = Math.max(0, paid - total);

  // 18. Сдача → Эдвин
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });

  // Дата оплаты (авто = сегодня)
  const [paymentDate, setPaymentDate] = useState(todayStr());

  // Чаевые (как в продаже)
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });

  // 21. Комментарий (не обязателен)
  const [comment, setComment] = useState("");

  // 22. Фото
  const [photoAttached, setPhotoAttached] = useState(false);

  // 23. Этап
  const [stage, setStage] = useState("Сертификат продан");

  const canSave = !!(
    client.name &&
    client.phone &&
    guest &&
    certNumber &&
    (digital ? consultants.callManager && email : photoAttached)
  );

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: digital ? "online" : "offline",
      kind: digital ? "cert_digital" : "cert_plastic",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      callManager: digital ? consultants.callManager || undefined : undefined,
      clientName: client.name,
      clientPhone: client.phone,
      email: digital ? email || undefined : undefined,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel,
      guestName: guest,
      certificateNumber: certNumber,
      receivedAt,
      validUntil,
      paymentDate,
      items: useNominal
        ? [{ productId: "cert", name: `Сертификат №${certNumber}`, price: total, qty: 1 }]
        : items,
      payments,
      stage: s,
      comment,
      checkDiscount: discount || undefined,
      tips: tips.amount || undefined,
      changeStatus: change > 0 ? changeInfo.status : undefined,
      changeDestination: change > 0 && changeInfo.status === "pending" ? changeInfo.destination : undefined,
      photoAttached: digital ? undefined : photoAttached,
      total,
      paid,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title={digital ? "Сертификат (электронный)" : "Сертификат (пластиковый)"}
      subtitle={digital ? "Онлайн · отправка на e-mail" : "Оффлайн · кошелёк с балансом"}
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={paid} />}
      footer={
        <StageActions
          stages={CERT_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!canSave}
          onBack={onDone}
        />
      }
    >
      {/* Консультант + клиент + гость (объединённый блок) */}
      <Card>
        <SectionTitle>Консультант, клиент и гость</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} withCallManager={digital} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <Field label="Имя гостя (кому подарок)" required>
            <input
              className="input"
              value={guest}
              onChange={(e) => setGuest(e.target.value.replace(/[0-9]/g, ""))}
              placeholder="Кому покупается сертификат"
            />
          </Field>
          {digital && (
            <Field label="E-mail для отправки" required>
              <input
                type="email"
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="client@example.com"
              />
            </Field>
          )}
        </div>
      </Card>

      {/* Товары, стоимость и скидка */}
      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>

        {/* Переключатель: номинал кнопками / из каталога */}
        <div className="flex gap-2 mb-4">
          <button
            type="button"
            onClick={() => setUseNominal(true)}
            className={`chip ${useNominal ? "bg-gold/20 text-gold-soft border border-gold/40" : "bg-ink-700 text-mute"}`}
          >
            Выбрать номинал
          </button>
          <button
            type="button"
            onClick={() => setUseNominal(false)}
            className={`chip ${!useNominal ? "bg-gold/20 text-gold-soft border border-gold/40" : "bg-ink-700 text-mute"}`}
          >
            Из каталога
          </button>
        </div>

        {useNominal ? (
          <>
            <div className="flex flex-wrap gap-2 mb-3">
              {NOMINALS.map((n) => (
                <button
                  key={n}
                  onClick={() => { setNominal(n); setCustom(""); }}
                  className={`px-4 py-2.5 rounded-lg border font-semibold ${
                    !custom && nominal === n
                      ? "border-gold bg-gold/15 text-gold-soft"
                      : "border-ink-700 text-mute hover:text-white"
                  }`}
                >
                  {n.toLocaleString("ru-RU")} ₽
                </button>
              ))}
            </div>
            <div className="max-w-[220px]">
              <div className="field-label">или свободная сумма</div>
              <input
                className="input"
                inputMode="numeric"
                placeholder="введите сумму"
                value={custom}
                onChange={(e) => setCustom(e.target.value.replace(/[^\d]/g, ""))}
              />
            </div>
          </>
        ) : (
          <ProductPicker items={items} onChange={setItems} />
        )}

        {/* Доставка */}
        <div className="mt-4 max-w-[220px]">
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
          <TotalsBlock subtotal={nominalTotal} state={disc} onChange={setDisc} />
        </div>
      </Card>

      {/* Номер, дата получения, действителен до */}
      <Card>
        <SectionTitle>Данные сертификата</SectionTitle>
        <div className="grid sm:grid-cols-3 gap-4">
          <Field label="Номер сертификата" required>
            <input
              className="input"
              value={certNumber}
              onChange={(e) => setCertNumber(e.target.value)}
              placeholder="напр. 45630"
            />
          </Field>
          <Field label="Дата получения">
            <input
              type="date"
              className="input"
              value={receivedAt}
              onChange={(e) => handleReceivedAt(e.target.value)}
            />
          </Field>
          <Field label="Действителен до">
            <input
              type="date"
              className="input"
              value={validUntil}
              onChange={(e) => setValidUntil(e.target.value)}
            />
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
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-mute">Баланс сертификата после активации:</span>
          <span className="text-white font-semibold">{money(total)}</span>
        </div>
      </Card>

      {/* Источник + комментарий */}
      <Card>
        <SectionTitle>Источник и комментарий</SectionTitle>
        <SourceFields data={client} onChange={setClient} withPurpose={false} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} />
        </div>
      </Card>

      {/* Фото сертификата (только для пластикового) */}
      {!digital && (
        <Card>
          <SectionTitle>Фото сертификата</SectionTitle>
          <PhotoField
            attached={photoAttached}
            onChange={setPhotoAttached}
            label="Фото сертификата"
          />
        </Card>
      )}

      {toast}
    </FormShell>
  );
}
