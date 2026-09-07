import { useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal, cartItemsDiscount } from "../../components/ProductPicker";
import {
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
  tipsDealFields,
  useSaved,
  type ChangeInfo,
  type ClientData,
  type ConsultantData,
  type DiscountState,
  type TipsInfo,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { formatPhone } from "../../lib/format";
import type { CartItem, Payment } from "../../data/types";

const SENDER = "MANSBAND · г. Москва, Спартаковская пл., д. 14, стр. 2 · +7 (495) 000-00-00";
const DEFAULT_CALL_MANAGER = "Женя";

/** Частые города СДЭК — выбор из списка; если нет в списке — оставляем введённое. */
const DELIVERY_CITIES = [
  "Москва",
  "Санкт-Петербург",
  "Новосибирск",
  "Екатеринбург",
  "Казань",
  "Нижний Новгород",
  "Челябинск",
  "Самара",
  "Омск",
  "Ростов-на-Дону",
  "Уфа",
  "Красноярск",
  "Воронеж",
  "Пермь",
  "Волгоград",
  "Краснодар",
  "Саратов",
  "Тюмень",
  "Тольятти",
  "Ижевск",
  "Барнаул",
  "Ульяновск",
  "Иркутск",
  "Хабаровск",
  "Ярославль",
  "Владивосток",
  "Махачкала",
  "Томск",
  "Оренбург",
  "Кемерово",
  "Новокузнецк",
  "Рязань",
  "Набережные Челны",
  "Астрахань",
  "Пенза",
  "Липецк",
  "Киров",
  "Чебоксары",
  "Калининград",
  "Тула",
  "Курск",
  "Сочи",
  "Ставрополь",
  "Улан-Удэ",
  "Тверь",
  "Магнитогорск",
  "Иваново",
  "Брянск",
  "Белгород",
  "Сургут",
  "Владимир",
  "Архангельск",
  "Чита",
  "Смоленск",
  "Калуга",
  "Череповец",
  "Саранск",
  "Вологда",
  "Якутск",
  "Грозный",
  "Подольск",
  "Химки",
  "Мытищи",
  "Балашиха",
  "Королёв",
  "Люберцы",
];

function CityCombobox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return DELIVERY_CITIES.slice(0, 12);
    return DELIVERY_CITIES.filter((c) => c.toLowerCase().includes(q)).slice(0, 12);
  }, [value]);

  const exactMatch = DELIVERY_CITIES.some((c) => c.toLowerCase() === value.trim().toLowerCase());

  return (
    <div className="relative" ref={wrapRef}>
      <input
        className="input"
        value={value}
        placeholder="Начните вводить или выберите из списка"
        autoComplete="off"
        onFocus={() => setOpen(true)}
        onBlur={() => {
          // Даём клику по option сработать до закрытия.
          setTimeout(() => setOpen(false), 150);
        }}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-20 mt-1 w-full max-h-56 overflow-auto rounded-lg border border-ink-600 bg-ink-900 shadow-lg">
          {filtered.map((c) => (
            <li key={c}>
              <button
                type="button"
                className="w-full text-left px-3 py-2 text-sm text-white hover:bg-ink-800"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(c);
                  setOpen(false);
                }}
              >
                {c}
              </button>
            </li>
          ))}
          {value.trim() && !exactMatch && (
            <li className="px-3 py-2 text-[12px] text-mute border-t border-ink-700">
              Оставим «{value.trim()}» — города нет в списке, это нормально
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

export function DeliveryForm({ onDone }: { onDone: () => void }) {
  const { activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const store = "Онлайн-магазин" as const;

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: activeConsultant,
    referredBy: "",
    callManager: DEFAULT_CALL_MANAGER,
  });

  const [city, setCity] = useState("");
  const [senderOpen, setSenderOpen] = useState(false);

  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");

  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  const [items, setItems] = useState<CartItem[]>([]);
  const [delivery, setDelivery] = useState("");

  const [disc, setDisc] = useState<DiscountState>({ discPct: "", discRub: "" });
  const subtotal = cartTotal(items);
  const deliveryAmount = Number(delivery) || 0;
  const discount = calcDiscount(subtotal, disc);
  const total = Math.max(0, subtotal - discount) + deliveryAmount;

  const [payments, setPayments] = useState<Payment[]>([]);
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const change = Math.max(0, paid - total);
  const [changeInfo, setChangeInfo] = useState<ChangeInfo>({ status: "issued", destination: "" });
  const [tips, setTips] = useState<TipsInfo>({ amount: 0, status: "issued", destination: "" });

  const [comment, setComment] = useState("");

  const missingRequired = [
    !city.trim() && "Город получателя",
    !recipientName && "ФИО получателя",
    !recipientPhone && "Телефон получателя",
    items.length === 0 && "Товары",
    ...changeTipsMissing(paid, total, changeInfo, tips),
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "online",
      kind: "delivery",
      consultant: consultants.consultant,
      callManager: DEFAULT_CALL_MANAGER,
      clientName: recipientName,
      clientPhone: recipientPhone,
      store,
      storeAddress: STORE_ADDRESS[store],
      deliveryCity: city.trim(),
      recipientName,
      recipientPhone,
      channel: client.channel,
      purpose: client.purpose,
      items,
      payments,
      stage: s,
      comment,
      paymentDate: lastPaymentDate(payments),
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
      title="Доставка (СДЭК)"
      subtitle="Онлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[store]}
      summary={<SummaryBar total={total} paid={paid} />}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Передан на сборку"]}
          stage="Передан на сборку"
          onStageChange={() => {}}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и call-менеджер</SectionTitle>
        <ConsultantFields
          data={consultants}
          onChange={(d) => setConsultants({ ...d, callManager: DEFAULT_CALL_MANAGER })}
        />
        <div className="mt-4 grid sm:grid-cols-2 gap-4">
          <Field label="Call-менеджер">
            <input className="input opacity-70" readOnly value={DEFAULT_CALL_MANAGER} />
          </Field>
          <Field label="Тип заказа">
            <input className="input opacity-70" readOnly value="Доставка СДЭК" />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>Получатель и адрес</SectionTitle>
        <Field label="Город получателя" required hint="Выберите из списка или введите свой">
          <CityCombobox value={city} onChange={setCity} />
        </Field>

        <button
          type="button"
          onClick={() => setSenderOpen((v) => !v)}
          className="mt-4 inline-flex items-center gap-1.5 text-sm text-mute hover:text-white"
        >
          {senderOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />} Отправитель
        </button>
        {senderOpen && (
          <div className="mt-2 text-[13px] text-mute bg-ink-900 border border-ink-700 rounded-lg p-3">{SENDER}</div>
        )}

        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="ФИО получателя" required>
            <input
              className="input"
              value={recipientName}
              onChange={(e) => setRecipientName(e.target.value.replace(/[0-9]/g, ""))}
              placeholder="Кому доставить"
            />
          </Field>
          <Field label="Телефон получателя" required>
            <input
              className="input"
              inputMode="tel"
              value={recipientPhone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => setRecipientPhone(formatPhone(e.target.value))}
            />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>Товары, стоимость и скидка</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
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
