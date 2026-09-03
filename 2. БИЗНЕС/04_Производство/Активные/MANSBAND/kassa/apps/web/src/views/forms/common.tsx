import { useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Camera,
  CheckCircle2,
  Coins,
  PlusCircle,
  Store as StoreIcon,
  Trash2,
  Undo2,
  Upload,
  UserCheck,
} from "lucide-react";
import { Button, Field } from "../../components/ui";
import { CHANNELS, CONSULTANTS, PURPOSES, SALE_STAGES } from "../../data/mock";
import { dateCompact, dateRu, formatPhone, money, moneyPlain } from "../../lib/format";
import { useStore } from "../../store";
import type { Deal, Payment } from "../../data/types";
import { PaymentBlock } from "../../components/PaymentBlock";
import { filesToAttachments, type PhotoAttachment } from "../../lib/photo";
import { api, USE_MOCK } from "../../api/client";

// ── Хелперы ──────────────────────────────────────────────────────────

/** Сегодня в формате YYYY-MM-DD. */
export function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Подпись под date-input: «6 июн (сб)». */
export function DateFieldHint({ value }: { value: string }) {
  if (!value) return null;
  return <p className="text-[12px] text-mute mt-1">{dateCompact(value)}</p>;
}

// ── Типы ────────────────────────────────────────────────────────────

export interface ClientData {
  name: string;
  phone: string;
  channel: string;
  purpose: string;
}

export interface ConsultantData {
  consultant: string;
  referredBy: string;
  callManager?: string;
}

// ── Блок клиента ─────────────────────────────────────────────────────

/**
 * Телефон (маска +7, только цифры) → имя.
 * Порядок: телефон левее/выше имени.
 * Схлопывание: при вводе телефона ищет клиента, подставляет имя.
 */
export function ClientFields({
  data,
  onChange,
  onFound,
  nameLabel = "Имя клиента",
}: {
  data: ClientData;
  onChange: (d: ClientData) => void;
  onFound?: (deal: Deal) => void;
  nameLabel?: string;
}) {
  const { findByPhone } = useStore();
  const [found, setFound] = useState<Deal | null>(null);
  const [amoName, setAmoName] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const lookupRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handlePhone(raw: string) {
    const phone = formatPhone(raw);
    const match = findByPhone(phone);
    setAmoName(null);
    if (match) {
      setFound(match);
      onChange({ ...data, phone, name: data.name || match.clientName });
      onFound?.(match);
    } else {
      setFound(null);
      onChange({ ...data, phone });
    }

    // Подстановка имени из amoCRM по телефону, если локально клиент не найден
    // и имя ещё не введено (не перезатираем ручной ввод).
    if (lookupRef.current) clearTimeout(lookupRef.current);
    const digits = phone.replace(/\D/g, "");
    if (USE_MOCK || match || digits.length !== 11) {
      setLookingUp(false);
      return;
    }
    lookupRef.current = setTimeout(async () => {
      setLookingUp(true);
      try {
        const res = await api.get<{ id: number; name: string }>(
          `/contacts/by-phone?phone=${encodeURIComponent(phone)}`
        );
        setAmoName(res.name);
        onChange({ ...data, phone, name: data.name || res.name });
      } catch {
        // контакт не найден в amoCRM — это ок, клиент новый
      } finally {
        setLookingUp(false);
      }
    }, 400);
  }

  return (
    <div className="grid sm:grid-cols-2 gap-4">
      <Field label="Телефон клиента" required>
        <input
          className="input"
          value={data.phone}
          placeholder="+7 (___) ___-__-__"
          inputMode="tel"
          onChange={(e) => handlePhone(e.target.value)}
        />
        {found && (
          <div className="mt-1 inline-flex items-center gap-1.5 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-2 py-0.5">
            <UserCheck size={12} /> Клиент найден: {found.clientName} (заявка #{found.number})
          </div>
        )}
        {!found && lookingUp && (
          <div className="mt-1 text-[12px] text-mute">Поиск клиента в amoCRM…</div>
        )}
        {!found && amoName && (
          <div className="mt-1 inline-flex items-center gap-1.5 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-2 py-0.5">
            <UserCheck size={12} /> Найден в amoCRM: {amoName}
          </div>
        )}
      </Field>
      <Field label={nameLabel} required>
        <input
          className="input"
          value={data.name}
          onChange={(e) => onChange({ ...data, name: e.target.value.replace(/[0-9]/g, "") })}
        />
      </Field>
    </div>
  );
}

// ── Консультанты ─────────────────────────────────────────────────────

/**
 * Блок: «Имя консультанта» (обяз.) + «Направивший консультант» (авто/ручной).
 * Ставится в самом верху формы (пункты 5–6 ТЗ).
 */
export function ConsultantFields({
  data,
  onChange,
  withCallManager = false,
}: {
  data: ConsultantData;
  onChange: (d: ConsultantData) => void;
  withCallManager?: boolean;
}) {
  const { activeConsultant } = useStore();
  const consultants = CONSULTANTS.filter((c) => c.role === "consultant").map((c) => c.name);
  const callManagers = CONSULTANTS.filter((c) => c.role === "callmanager").map((c) => c.name);

  return (
    <div className="grid sm:grid-cols-2 gap-4">
      <Field label="Консультант" required>
        <select
          className="input"
          value={data.consultant || activeConsultant}
          onChange={(e) => onChange({ ...data, consultant: e.target.value })}
        >
          {consultants.map((c) => <option key={c}>{c}</option>)}
        </select>
      </Field>
      <Field label="Направивший консультант">
        <input className="input opacity-70" readOnly value={data.referredBy || "—"} />
      </Field>
      {withCallManager && (
        <Field label="Call-менеджер" required>
          <select
            className="input"
            value={data.callManager ?? ""}
            onChange={(e) => onChange({ ...data, callManager: e.target.value })}
          >
            <option value="">— выбрать —</option>
            {callManagers.map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
      )}
    </div>
  );
}

// ── Источник и цель ───────────────────────────────────────────────────

/** Канал и цель — выносятся ВНИЗ формы (после оплаты). */
export function SourceFields({
  data,
  onChange,
  withChannel = true,
  withPurpose = true,
  channelFixed,
}: {
  data: ClientData;
  onChange: (d: ClientData) => void;
  withChannel?: boolean;
  withPurpose?: boolean;
  channelFixed?: string;
}) {
  const set = (patch: Partial<ClientData>) => onChange({ ...data, ...patch });
  return (
    <div className="grid sm:grid-cols-2 gap-4">
      {withChannel && (
        channelFixed ? (
          <Field label="Источник рекламы">
            <input className="input opacity-70" readOnly value={channelFixed} />
          </Field>
        ) : (
          <Field label="Источник рекламы" required>
            <select className="input" value={data.channel} onChange={(e) => set({ channel: e.target.value })}>
              <option value="">— выбрать —</option>
              {CHANNELS.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
        )
      )}
      {withPurpose && (
        <Field label="На какой случай / цель" required>
          <select className="input" value={data.purpose} onChange={(e) => set({ purpose: e.target.value })}>
            <option value="">— выбрать —</option>
            {PURPOSES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </Field>
      )}
    </div>
  );
}

// ── Скидка на весь чек ────────────────────────────────────────────────

export interface DiscountState {
  discPct: string;
  discRub: string;
}

export function calcDiscount(subtotal: number, state: DiscountState): number {
  if (state.discRub) return Number(state.discRub);
  if (state.discPct) return Math.round((subtotal * Number(state.discPct)) / 100);
  return 0;
}

// ── Блок «Товары, стоимость и скидка»: 3 суммы в ряд ──────────────────

function SumTile({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: string;
  tone?: "muted" | "discount" | "strong";
}) {
  const box =
    tone === "strong"
      ? "bg-gold/15 border-gold/40"
      : tone === "discount"
        ? "bg-ink-900 border-ink-700"
        : "bg-ink-900 border-ink-700";
  const val =
    tone === "strong"
      ? "text-gold-soft text-lg"
      : tone === "discount"
        ? "text-amber-200"
        : "text-white";
  return (
    <div className={`rounded-lg border px-3 py-2.5 text-center ${box}`}>
      <div className="field-label mb-0.5">{label}</div>
      <div className={`font-bold ${val}`}>{value}</div>
    </div>
  );
}

/**
 * Скидка на весь чек (% или ₽) + три компактные суммы в ряд:
 * Общая стоимость · Скидка (₽) · Итого со скидкой.
 * При скидке в % сумма скидки всё равно показывается в ₽ (в плитке «Скидка»).
 * Ставится ВНУТРИ карточки товаров, после позиций.
 */
export function TotalsBlock({
  subtotal,
  state,
  onChange,
}: {
  subtotal: number;
  state: DiscountState;
  onChange: (s: DiscountState) => void;
}) {
  const discount = calcDiscount(subtotal, state);
  const total = Math.max(0, subtotal - discount);
  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Скидка на весь чек %">
          <input
            className="input"
            inputMode="numeric"
            placeholder="0"
            value={state.discPct}
            onChange={(e) => onChange({ discPct: e.target.value.replace(/[^\d]/g, ""), discRub: "" })}
          />
        </Field>
        <Field label="Скидка ₽">
          <input
            className="input"
            inputMode="numeric"
            placeholder="0"
            value={state.discRub}
            onChange={(e) => onChange({ discRub: e.target.value.replace(/[^\d]/g, ""), discPct: "" })}
          />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <SumTile label="Общая стоимость" value={money(subtotal)} />
        <SumTile
          label={state.discPct ? `Скидка ${state.discPct}%` : "Скидка"}
          value={discount > 0 ? `−${money(discount)}` : money(0)}
          tone="discount"
        />
        <SumTile label="Итого" value={money(total)} tone="strong" />
      </div>
    </div>
  );
}

// ── Sticky-строка снизу: Сумма · Оплачено · Остаток ──────────────────

export function SummaryBar({ total, paid }: { total: number; paid: number }) {
  const remainder = Math.max(0, total - paid);
  const cell = (label: string, value: string, valueClass: string) => (
    <div className="flex-1 min-w-0 text-center px-1">
      <div className="text-[11px] sm:text-xs uppercase tracking-wide text-mute mb-1.5">{label}</div>
      <div className={`text-xl sm:text-2xl font-extrabold tabular-nums leading-none ${valueClass}`}>
        {value}
      </div>
    </div>
  );
  return (
    <div className="flex items-stretch justify-between gap-2 sm:gap-4 rounded-xl bg-ink-900 border border-ink-600 px-3 py-4 sm:px-5 sm:py-5 shadow-[0_-4px_24px_rgba(0,0,0,0.35)]">
      {cell("Сумма, ₽", moneyPlain(total), "text-white")}
      {cell("Оплачено, ₽", moneyPlain(paid), "text-white")}
      {cell(
        "Остаток, ₽",
        moneyPlain(remainder),
        remainder > 0 ? "text-amber-300" : "text-emerald-300",
      )}
    </div>
  );
}

// ── Сдача → Эдвин ─────────────────────────────────────────────────────

export interface ChangeInfo {
  status: "issued" | "pending";
  destination: string;
}

/**
 * Показывается когда paid > total (есть сдача).
 * «Выдано» — консультант выдал сам.
 * «Не выдано» — нужно перевести через Эдвина: поле телефон/карта + банк.
 */
export function ChangeBlock({
  change,
  info,
  onChange,
}: {
  change: number;
  info: ChangeInfo;
  onChange: (i: ChangeInfo) => void;
}) {
  if (change <= 0) return null;
  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="text-[13px] font-semibold text-white">
        Сдача {money(change)} — статус выдачи
      </div>
      <div className="flex gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => onChange({ ...info, status: "issued" })}
          className={`chip ${info.status === "issued" ? "bg-white text-ink-950 font-semibold" : "bg-ink-700 text-mute hover:text-white"}`}
        >
          Выдано клиенту
        </button>
        <button
          type="button"
          onClick={() => onChange({ ...info, status: "pending" })}
          className={`chip ${info.status === "pending" ? "bg-amber-400/20 text-amber-200 border border-amber-400/40" : "bg-ink-700 text-mute hover:text-white"}`}
        >
          Получить от Mansband
        </button>
      </div>
      {info.status === "pending" && (
        <Field label="Куда переводить" required hint="Номер телефона или карты · Банк">
          <input
            className="input"
            value={info.destination}
            placeholder="+7 9XX или 2202 2004 XXXX · Сбербанк"
            onChange={(e) => onChange({ ...info, destination: e.target.value })}
          />
          {!info.destination.trim() && (
            <div className="mt-1 text-[11px] text-amber-300/80">Обязательно — иначе Эдвин не знает, куда переводить</div>
          )}
        </Field>
      )}
      {info.status === "issued" && (
        <div className="text-[12px] text-mute">Сдача выдана, в очередь Эдвина не попадёт.</div>
      )}
    </div>
  );
}

// ── Чаевые ─────────────────────────────────────────────────────────────

export interface TipsInfo {
  amount: number;
  status: "issued" | "pending";
  destination: string;
}

/**
 * Чаевые из сдачи. Ставится В КОНЦЕ блока оплаты (после сдачи).
 * «Сдача» сверху не меняется — меняется только фактически возвращаемое клиенту.
 * Консультант: «Забрал наличкой» сразу ИЛИ «Получить от Mansband» (имя ответственного
 * за заявку подставляется автоматически, read-only; реквизиты не вводим).
 */
export function TipsBlock({
  change,
  info,
  onChange,
  responsible,
}: {
  change: number;
  info: TipsInfo;
  onChange: (i: TipsInfo) => void;
  responsible?: string;
}) {
  if (change <= 0) return null;
  const toReturn = Math.max(0, change - info.amount);
  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
        <Coins size={15} className="text-gold" /> Чаевые из сдачи
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          className="input w-[150px]"
          inputMode="numeric"
          placeholder="0"
          value={info.amount || ""}
          onChange={(e) =>
            onChange({ ...info, amount: Math.min(change, Number(e.target.value.replace(/[^\d]/g, "")) || 0) })
          }
        />
        <button
          type="button"
          onClick={() => onChange({ ...info, amount: Math.round(change) })}
          className="chip bg-ink-700 text-mute hover:text-white"
        >
          вся сдача {money(change)}
        </button>
        {info.amount > 0 && (
          <button
            type="button"
            onClick={() => onChange({ ...info, amount: 0 })}
            className="chip bg-ink-700 text-mute hover:text-white"
          >
            сбросить
          </button>
        )}
      </div>

      {info.amount > 0 && (
        <>
          <div className="text-[12px] text-mute">
            Сдача {money(change)} фиксирована · чаевые {money(info.amount)} ·{" "}
            <span className="text-white">фактически к возврату клиенту {money(toReturn)}</span>
          </div>
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => onChange({ ...info, status: "issued" })}
              className={`chip ${info.status === "issued" ? "bg-white text-ink-950 font-semibold" : "bg-ink-700 text-mute hover:text-white"}`}
            >
              Забрал наличкой
            </button>
            <button
              type="button"
              onClick={() =>
                onChange({ ...info, status: "pending", destination: responsible || "" })
              }
              className={`chip ${info.status === "pending" ? "bg-amber-400/20 text-amber-200 border border-amber-400/40" : "bg-ink-700 text-mute hover:text-white"}`}
            >
              Получить от Mansband
            </button>
          </div>
          {info.status === "pending" && (
            <Field label="Кому перевести чаевые" hint="Ответственный за заявку (авто)">
              <input
                className="input opacity-70"
                readOnly
                value={info.destination || responsible || ""}
              />
            </Field>
          )}
        </>
      )}
    </div>
  );
}

// ── Дата оплаты ──────────────────────────────────────────────────────

/** Дата оплаты: фактическая (авто = сегодня), изменить нельзя. Ставится в карточке «Оплата». */
export function PaymentDateField({ value }: { value: string; onChange?: (v: string) => void }) {
  return (
    <div className="max-w-[220px]">
      <Field label="Дата оплаты">
        <input type="date" className="input opacity-70" readOnly value={value} />
        <DateFieldHint value={value} />
      </Field>
    </div>
  );
}

// ── Доплата (вторая оплата на остаток) ───────────────────────────────

export interface TopUpInfo {
  open: boolean;
  payments: Payment[];
  changeInfo: ChangeInfo;
}

export const emptyTopUp: TopUpInfo = {
  open: false,
  payments: [],
  changeInfo: { status: "issued", destination: "" },
};

/**
 * Блок «Доплата»: кнопка раскрывает вторую оплату на остаток.
 * Для Аренды / Отложки / Обмена — когда клиент доплачивает позже.
 */
export function TopUpBlock({
  remainder,
  info,
  onChange,
}: {
  remainder: number;
  info: TopUpInfo;
  onChange: (i: TopUpInfo) => void;
}) {
  const topUpPaid = info.payments.reduce((s, p) => s + p.amount, 0);
  const topUpChange = Math.max(0, topUpPaid - remainder);

  if (!info.open) {
    if (remainder <= 0) return null;
    return (
      <button
        type="button"
        onClick={() => onChange({ ...info, open: true })}
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-gold-soft hover:text-white"
      >
        <PlusCircle size={16} /> Доплата (остаток {money(remainder)})
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-[13px] font-semibold text-white">
          Доплата · к доплате {money(Math.max(0, remainder))}
        </div>
        <button
          type="button"
          onClick={() => onChange({ ...emptyTopUp })}
          className="text-[12px] text-mute hover:text-white"
        >
          свернуть
        </button>
      </div>
      <PaymentBlock
        total={Math.max(0, remainder)}
        payments={info.payments}
        onChange={(p) => onChange({ ...info, payments: p })}
      />
      {topUpChange > 0 && (
        <ChangeBlock
          change={topUpChange}
          info={info.changeInfo}
          onChange={(ci) => onChange({ ...info, changeInfo: ci })}
        />
      )}
    </div>
  );
}

// ── Возврат денег → Эдвин ────────────────────────────────────────────

export interface ReturnInfo {
  status: "issued" | "pending";
  destination: string;
}

/**
 * Возврат денег клиенту. Аналог ChangeBlock, но для возврата.
 * «Не возвращено» → задача в очередь Эдвина (kind refund).
 */
export function ReturnBlock({
  amount,
  info,
  onChange,
}: {
  amount: number;
  info: ReturnInfo;
  onChange: (i: ReturnInfo) => void;
}) {
  if (amount <= 0) return null;
  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
        <Undo2 size={15} className="text-gold" /> Возврат клиенту {money(amount)}
      </div>
      <div className="flex gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => onChange({ ...info, status: "issued" })}
          className={`chip ${info.status === "issued" ? "bg-white text-ink-950 font-semibold" : "bg-ink-700 text-mute hover:text-white"}`}
        >
          Возвращено наличными
        </button>
        <button
          type="button"
          onClick={() => onChange({ ...info, status: "pending" })}
          className={`chip ${info.status === "pending" ? "bg-amber-400/20 text-amber-200 border border-amber-400/40" : "bg-ink-700 text-mute hover:text-white"}`}
        >
          Возврат через Эдвина
        </button>
      </div>
      {info.status === "pending" && (
        <Field label="Куда вернуть" required hint="Номер телефона или карты · Банк">
          <input
            className="input"
            value={info.destination}
            placeholder="+7 9XX или 2202 2004 XXXX · Сбербанк"
            onChange={(e) => onChange({ ...info, destination: e.target.value })}
          />
          {!info.destination.trim() && (
            <div className="mt-1 text-[11px] text-amber-300/80">Обязательно — иначе Эдвин не знает, куда переводить</div>
          )}
        </Field>
      )}
      {info.status === "issued" && (
        <div className="text-[12px] text-mute">Деньги возвращены на месте, в очередь Эдвина не попадёт.</div>
      )}
    </div>
  );
}

// ── Адрес магазина ───────────────────────────────────────────────────

/** Чип «Адрес магазина» — авто из активного магазина (readonly). */
export function StoreAddressChip({ address }: { address: string }) {
  return (
    <span className="chip bg-ink-800 text-mute-soft inline-flex items-center gap-1.5">
      <StoreIcon size={12} className="text-gold" /> {address}
    </span>
  );
}

// ── Фото ───────────────────────────────────────────────────────────────

/**
 * Реальная фотофиксация: съёмка через камеру устройства (capture=environment)
 * или загрузка из галереи, мультивыбор, превью с удалением по одной.
 * `photos` — источник истины (массив), совместимость с прежним boolean-флагом
 * форм обеспечивается через `photos.length > 0`.
 */
export function PhotoField({
  photos,
  onChange,
  label = "Фото",
  required = true,
}: {
  photos: PhotoAttachment[];
  onChange: (v: PhotoAttachment[]) => void;
  label?: string;
  required?: boolean;
}) {
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    try {
      const added = await filesToAttachments(files);
      onChange([...photos, ...added]);
    } finally {
      setBusy(false);
    }
  }

  function remove(id: string) {
    onChange(photos.filter((p) => p.id !== id));
  }

  return (
    <div>
      <div className="field-label flex items-center gap-1">
        {label} {required && <span className="text-gold">*</span>}
      </div>

      {photos.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {photos.map((p) => (
            <div key={p.id} className="relative w-20 h-20 rounded-lg overflow-hidden border border-ink-600 group">
              <img src={p.dataUrl} alt={p.filename} className="w-full h-full object-cover" />
              <button
                type="button"
                onClick={() => remove(p.id)}
                className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 flex items-center justify-center text-white transition"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      )}

      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => { void handleFiles(e.target.files); e.target.value = ""; }}
      />
      <input
        ref={galleryInput}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => { void handleFiles(e.target.files); e.target.value = ""; }}
      />

      <div className="flex gap-2 flex-wrap">
        <button
          type="button"
          disabled={busy}
          onClick={() => cameraInput.current?.click()}
          className={`flex-1 min-w-[140px] flex items-center justify-center gap-2 rounded-lg border-2 border-dashed py-4 transition ${
            photos.length > 0
              ? "border-white/40 bg-white/10 text-white"
              : "border-ink-600 text-mute hover:border-gold/50"
          }`}
        >
          <Camera size={18} /> Сделать фото
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => galleryInput.current?.click()}
          className="flex-1 min-w-[140px] flex items-center justify-center gap-2 rounded-lg border-2 border-dashed border-ink-600 text-mute hover:border-gold/50 py-4 transition"
        >
          <Upload size={16} /> Загрузить из галереи
        </button>
      </div>
      {photos.length > 0 && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-emerald-300/90">
          <CheckCircle2 size={13} /> {photos.length === 1 ? "1 фото прикреплено" : `${photos.length} фото прикреплено`}
        </div>
      )}
    </div>
  );
}

// ── Этап + кнопки сохранения ──────────────────────────────────────────

/**
 * Footer формы: выбор этапа → «Сохранить заявку».
 * Отдельная кнопка «Провести в Успех» (если successStage передан).
 */
export function StageActions({
  stages,
  stage,
  onStageChange,
  onSave,
  saved,
  disabled = false,
  onBack,
  successStage,
  successDisabled,
  successHint,
  onSuccess,
}: {
  stages: string[];
  stage: string;
  onStageChange: (s: string) => void;
  onSave: (stage: string) => void;
  saved: boolean;
  disabled?: boolean;
  onBack: () => void;
  successStage?: string;
  successDisabled?: boolean;
  successHint?: string;
  onSuccess?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 justify-end flex-wrap min-h-[72px] py-1">
      {successHint && <span className="text-[12px] text-amber-300/90 mr-auto">{successHint}</span>}
      <select
        className="input w-auto text-sm"
        value={stage}
        onChange={(e) => onStageChange(e.target.value)}
        disabled={saved}
      >
        {stages.map((s) => <option key={s}>{s}</option>)}
      </select>
      <Button variant="outline" className="py-3" onClick={onBack} disabled={saved}>Отмена</Button>
      <Button variant="subtle" className="py-3" disabled={disabled || saved} onClick={() => onSave(stage)}>
        Сохранить заявку
      </Button>
      {successStage && onSuccess && (
        <Button className="py-3" disabled={successDisabled || saved} onClick={onSuccess}>
          Провести в «{successStage}»
        </Button>
      )}
    </div>
  );
}

// ── Комментарий ────────────────────────────────────────────────────────

export function CommentField({
  value,
  onChange,
  required = false,
}: {
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
}) {
  return (
    <Field label="Комментарий" required={required}>
      <textarea
        className="input min-h-[72px]"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

// ── FormShell ──────────────────────────────────────────────────────────

export function FormShell({
  title,
  subtitle,
  onBack,
  children,
  footer,
  meta,
  storeAddress,
  summary,
}: {
  title: string;
  subtitle?: string;
  onBack: () => void;
  children: ReactNode;
  footer: ReactNode;
  meta?: { number: number; createdAt: string };
  storeAddress?: string;
  summary?: ReactNode;
}) {
  return (
    <div className="max-w-3xl mx-auto">
      <button
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-mute hover:text-white text-sm mb-4"
      >
        <ArrowLeft size={16} /> Назад к выбору
      </button>
      <div className="mb-5">
        <div className="flex items-center gap-2 flex-wrap">
          <h1 className="text-2xl font-extrabold text-white">{title}</h1>
          {meta && (
            <span className="chip bg-white/10 text-white/90 font-mono">
              Заявка №{meta.number} · {dateRu(meta.createdAt)}
            </span>
          )}
          {storeAddress && <StoreAddressChip address={storeAddress} />}
        </div>
        {subtitle && <p className="text-mute text-sm mt-0.5">{subtitle}</p>}
      </div>
      <div className="space-y-5">{children}</div>
      <div className="sticky bottom-0 mt-6 -mx-1 pt-5 pb-6 sm:pb-8 bg-gradient-to-t from-ink-950 via-ink-950 to-transparent">
        {summary && <div className="mb-5">{summary}</div>}
        {footer}
      </div>
    </div>
  );
}

// ── useSaved ────────────────────────────────────────────────────────────

export function useSaved() {
  const [saved, setSaved] = useState(false);
  const toast = saved ? (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 bg-white text-ink-950 font-semibold px-5 py-3 rounded-xl shadow-glow animate-[fadein_0.2s]">
      <CheckCircle2 size={18} /> Заявка сохранена и отправлена в amoCRM
    </div>
  ) : null;
  return { saved, setSaved, toast };
}

// ── SectionTitle ────────────────────────────────────────────────────────

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="text-[13px] uppercase tracking-wider text-gold-soft font-bold mb-3">
      {children}
    </h3>
  );
}

// re-export SALE_STAGES для использования в формах
export { SALE_STAGES };
