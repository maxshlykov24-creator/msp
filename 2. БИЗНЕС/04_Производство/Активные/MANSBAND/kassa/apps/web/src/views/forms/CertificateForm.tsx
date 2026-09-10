import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useStore } from "../../store";
import { ApiError, api, USE_MOCK } from "../../api/client";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
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
  TotalsBlock,
  calcDiscount,
  changeDealFields,
  changeTipsMissing,
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
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { money } from "../../lib/format";
import { attachmentsToUpload, type PhotoAttachment } from "../../lib/photo";
import type { CartItem, Payment } from "../../data/types";

const NOMINALS = [5000, 10000, 15000, 20000, 25000, 30000];
// «Ждет товар» — только для пластика: бланк может лежать на другой точке.
const CERT_STAGES_DIGITAL = ["Сертификат оплачен", "Успех", "Провал"];
const CERT_STAGES_PLASTIC = ["Сертификат оплачен", "Ждет товар", "Успех", "Провал"];
const DEFAULT_CALL_MANAGER = "Женя";

/** Демо-режим без бэкенда: номер подбирается локально по реестру в сторе. */
function localDigitalNumber(taken: string[]): string {
  const busy = new Set(taken);
  for (let i = 0; i < 50; i++) {
    const n = String(100000 + Math.floor(Math.random() * 900000));
    if (!busy.has(n)) return n;
  }
  return String(100000 + Math.floor(Math.random() * 900000));
}

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
  const { activeStore, activeConsultant, addDeal, nextNumber, certificates } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
    callManager: digital ? DEFAULT_CALL_MANAGER : "",
  });

  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });
  const [email, setEmail] = useState("");
  const [guest, setGuest] = useState("");

  // Номинал сертификата + доп. позиции из каталога (как в продаже)
  const [nominal, setNominal] = useState<number>(15000);
  const [custom, setCustom] = useState("");
  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");

  const [certNumber, setCertNumber] = useState("");
  const [generating, setGenerating] = useState(false);
  const [numberError, setNumberError] = useState<string | null>(null);
  const [numberHint, setNumberHint] = useState<string | null>(null);

  async function generateNumber() {
    setGenerating(true);
    setNumberError(null);
    setNumberHint(null);
    // Сразу локальный номер — поле не пустое, если API завис/отвалился.
    const local = localDigitalNumber(certificates.map((c) => c.number));
    setCertNumber(local);
    if (USE_MOCK) {
      setGenerating(false);
      return;
    }
    try {
      const res = await api.post<{ number: string }>("/certificates/next-number");
      if (res?.number && /^\d{6}$/.test(res.number)) {
        setCertNumber(res.number);
        setNumberHint(null);
      } else {
        setNumberHint("Номер сгенерирован на устройстве");
      }
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 401
          ? "Сессия сброшена — номер локальный. Перелогиньтесь и нажмите «Сгенерировать» ещё раз"
          : "Сервер не ответил — номер сгенерирован на устройстве. При необходимости нажмите «Сгенерировать» снова";
      setNumberError(msg);
    } finally {
      setGenerating(false);
    }
  }

  // Электронный сертификат: номер сразу при открытии формы.
  useEffect(() => {
    if (digital) void generateNumber();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только при монтировании
  }, [digital]);

  const [receivedAt, setReceivedAt] = useState(todayStr());
  const [validUntil, setValidUntil] = useState(addYear(todayStr()));

  function handleReceivedAt(val: string) {
    setReceivedAt(val);
    setValidUntil(addYear(val));
  }

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });

  const certNominal = custom ? Number(custom) || 0 : nominal;
  const subtotal = certNominal + cartTotal(items);
  const deliveryAmount = Number(delivery) || 0;
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount) + deliveryAmount;

  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const change = Math.max(0, paid - total);

  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });
  const [comment, setComment] = useState("");
  const [photos, setPhotos] = useState<PhotoAttachment[]>([]);
  const [stage, setStage] = useState("Сертификат оплачен");

  const missingRequired = useMemo(() => {
    const m: string[] = [];
    if (!client.phone) m.push("Телефон клиента");
    if (!client.name) m.push("Имя клиента");
    if (!guest) m.push("Имя гостя");
    if (!certNumber) {
      m.push(digital ? "Номер сертификата — нажмите «Сгенерировать»" : "Номер сертификата");
    } else if (digital ? !/^\d{6}$/.test(certNumber) : !/^\d{5,6}$/.test(certNumber)) {
      m.push(digital ? "Номер сертификата: 6 цифр" : "Номер сертификата: 5–6 цифр");
    } else if (certificates.some((c) => c.number === certNumber)) {
      m.push("Номер сертификата уже используется");
    }
    if (!certNominal) m.push("Номинал сертификата");
    if (stage !== "Провал" && paid < total) m.push("Полная оплата сертификата");
    if (!receivedAt || !validUntil || validUntil <= receivedAt) m.push("Корректный срок действия сертификата");
    if (stage === "Успех") m.push(...sourceMissingForSuccess(client, { withPurpose: false }));
    if (digital) {
      if (!email) m.push("E-mail для отправки");
    } else if (photos.length === 0) {
      m.push("Фото сертификата");
    }
    m.push(...changeTipsMissing(paid, total, changeInfo, tips));
    return m;
  }, [client.phone, client.name, client.channel, guest, certNumber, certNominal, digital, email, photos.length, certificates, receivedAt, validUntil, stage, paid, total, changeInfo, tips]);

  const canSave = missingRequired.length === 0;

  function save(s: string) {
    const certLine: CartItem = {
      productId: "cert",
      name: `Сертификат №${certNumber}`,
      price: certNominal,
      qty: 1,
    };
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: digital ? "online" : "offline",
      kind: digital ? "cert_digital" : "cert_plastic",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      callManager: digital ? DEFAULT_CALL_MANAGER : undefined,
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
      paymentDate: lastPaymentDate(payments),
      items: [certLine, ...items],
      payments,
      stage: s,
      comment,
      checkDiscount: discount || undefined,
      ...tipsDealFields(tips, change),
      ...changeDealFields(Math.max(0, change - (tips.amount || 0)), changeInfo),
      photoAttached: digital ? undefined : photos.length > 0,
      total,
      paid,
    }, digital ? undefined : attachmentsToUpload(photos));
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      onBack={onDone}
      title={digital ? "Сертификат (электронный)" : "Сертификат (пластиковый)"}
      subtitle={digital ? "Онлайн" : "Оффлайн"}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      summary={<SummaryBar total={total} paid={paid} />}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={digital ? CERT_STAGES_DIGITAL : CERT_STAGES_PLASTIC}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!canSave}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант, клиент и гость</SectionTitle>
        <ConsultantFields
          data={consultants}
          onChange={(d) =>
            setConsultants(digital ? { ...d, callManager: DEFAULT_CALL_MANAGER } : d)
          }
        />
        {digital && (
          <div className="mt-4 max-w-sm">
            <Field label="Call-менеджер">
              <input className="input opacity-70" readOnly value={DEFAULT_CALL_MANAGER} />
            </Field>
          </div>
        )}
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <Field label="Имя гостя" required>
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

      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>

        <div className="field-label mb-2">Номинал сертификата</div>
        <div className="flex flex-wrap gap-2 mb-3">
          {NOMINALS.map((n) => (
            <button
              key={n}
              type="button"
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
        <div className="max-w-[220px] mb-5">
          <div className="field-label">или свободная сумма</div>
          <input
            className="input"
            inputMode="numeric"
            placeholder="введите сумму"
            value={custom}
            onChange={(e) => setCustom(e.target.value.replace(/[^\d]/g, ""))}
          />
        </div>

        <div className="field-label mb-2">Дополнительно из каталога</div>
        <ProductPicker items={items} onChange={setItems} />

        <div className="mt-4 max-w-[220px]">
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
          />
        </div>
      </Card>

      <Card>
        <SectionTitle>Данные сертификата</SectionTitle>
        <div className="grid sm:grid-cols-3 gap-4">
          <div className={digital ? "sm:col-span-2" : undefined}>
            <Field label="Номер сертификата" required>
              <div className={`flex gap-2 ${digital ? "flex-col sm:flex-row sm:items-stretch" : ""}`}>
                <input
                  className={`input min-w-0 flex-1 ${
                    digital
                      ? certNumber
                        ? "font-mono text-xl tracking-[0.2em] tabular-nums"
                        : "text-[13px] tracking-normal placeholder:text-[13px]"
                      : ""
                  }`}
                  value={certNumber}
                  onChange={(e) => {
                    if (digital) return;
                    setCertNumber(e.target.value.replace(/\D/g, "").slice(0, 6));
                    setNumberError(null);
                  }}
                  placeholder={digital ? "нажмите «Сгенерировать»" : "напр. 45630"}
                  readOnly={digital}
                  inputMode="numeric"
                  autoComplete="off"
                />
                {digital && (
                  <button
                    type="button"
                    onClick={() => void generateNumber()}
                    disabled={generating}
                    className="shrink-0 inline-flex items-center justify-center gap-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 disabled:opacity-60 text-white font-semibold px-3 py-2"
                  >
                    <RefreshCw size={15} className={generating ? "animate-spin" : ""} />
                    {certNumber ? "Сгенерировать снова" : "Сгенерировать"}
                  </button>
                )}
              </div>
              <div className={`text-[11px] mt-1 ${numberError ? "text-amber-300/90" : "text-mute"}`}>
                {numberError ??
                  numberHint ??
                  (digital ? "Только генерация · 6 цифр" : "Только цифры; номер должен быть уникальным")}
              </div>
            </Field>
          </div>
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

      <Card>
        <SectionTitle>Оплата</SectionTitle>
        <div className="mb-3 flex items-center justify-between text-sm rounded-lg bg-ink-900/50 border border-ink-700 px-3 py-2">
          <span className="text-mute">Баланс сертификата после активации</span>
          <span className="text-white font-semibold">{money(certNominal)}</span>
        </div>
        <p className="hint-only text-[12px] text-mute mb-3">
          Если клиент платит больше номинала — появится сдача и чаевые. На баланс сертификата уходит только номинал.
        </p>
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

      <Card>
        <SectionTitle>Источник и комментарий</SectionTitle>
        <SourceFields data={client} onChange={setClient} withPurpose={false} required={stage === "Успех"} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} />
        </div>
      </Card>

      {!digital && (
        <Card>
          <SectionTitle>Фото сертификата</SectionTitle>
          <PhotoField
            photos={photos}
            onChange={setPhotos}
            label="Фото сертификата"
          />
        </Card>
      )}

      {toast}
    </FormShell>
  );
}
