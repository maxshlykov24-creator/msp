import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { CHANNELS, CONSULTANTS, HIDDEN_STAGES, PURPOSES, SALE_STAGES } from "../../data/mock";
import { dateCompact, dateRu, formatPhone, money, moneyPlain } from "../../lib/format";
import { useStore } from "../../store";
import type { CartItem, Deal, Payment, Payout } from "../../data/types";
import {
  SARY_BONUS,
  isSuitCategory,
  mansbandPayoutAmount,
  normalizePayouts,
  selfPayouts,
} from "@kassa/shared";
import { PaymentBlock } from "../../components/PaymentBlock";
import { PayoutRows } from "../../components/PayoutRows";
import { filesToAttachments, type PhotoAttachment } from "../../lib/photo";
import { api, USE_MOCK } from "../../api/client";
import { useAppSettings } from "../../lib/appSettings";

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
  /** Сарафан: телефон друга, который направил клиента. */
  saryPhone?: string;
  /** Имя друга, найденное по телефону (пусто — в базе нет). */
  saryClient?: string;
  /** Бонус сарафана, применённый к чеку. */
  saryBonus?: number;
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
  phoneRequired = true,
  nameRequired = true,
}: {
  data: ClientData;
  onChange: (d: ClientData) => void;
  onFound?: (deal: Deal) => void;
  nameLabel?: string;
  phoneRequired?: boolean;
  nameRequired?: boolean;
}) {
  const { findByPhone } = useStore();
  const [found, setFound] = useState<Deal | null>(null);
  const [amoName, setAmoName] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const lookupRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLookupDigitsRef = useRef<string>("");
  const dataRef = useRef(data);
  dataRef.current = data;

  function handlePhone(raw: string) {
    const phone = formatPhone(raw);
    const match = findByPhone(phone);
    const digits = phone.replace(/\D/g, "");

    if (match) {
      setFound(match);
      setAmoName(null);
      lastLookupDigitsRef.current = "";
      onChange({ ...dataRef.current, phone, name: dataRef.current.name || match.clientName });
      onFound?.(match);
    } else {
      setFound(null);
      onChange({ ...dataRef.current, phone });
    }

    if (lookupRef.current) clearTimeout(lookupRef.current);
    // 10+ цифр; полный РФ — 11
    if (USE_MOCK || match || digits.length < 10) {
      setLookingUp(false);
      return;
    }
    const lookupDigits = digits.length >= 11 ? digits.slice(0, 11) : digits;
    // Не сбрасываем «Найден в amoCRM» при повторном onChange с тем же номером (iOS)
    if (lookupDigits !== lastLookupDigitsRef.current) {
      setAmoName(null);
    }

    lookupRef.current = setTimeout(async () => {
      setLookingUp(true);
      try {
        const res = await api.get<{ id: number; name: string }>(
          `/contacts/by-phone?phone=${encodeURIComponent(lookupDigits)}`
        );
        if (res?.name) {
          lastLookupDigitsRef.current = lookupDigits;
          setAmoName(res.name);
          const cur = dataRef.current;
          onChange({
            ...cur,
            phone,
            name: cur.name?.trim() ? cur.name : res.name,
          });
        }
      } catch {
        if (lookupDigits !== lastLookupDigitsRef.current) setAmoName(null);
      } finally {
        setLookingUp(false);
      }
    }, 450);
  }

  return (
    <div className="grid sm:grid-cols-2 gap-4">
      <Field label="Телефон клиента" required={phoneRequired}>
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
      <Field label={nameLabel} required={nameRequired}>
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
  allowEmptyConsultant = false,
}: {
  data: ConsultantData;
  onChange: (d: ConsultantData) => void;
  withCallManager?: boolean;
  /**
   * Отложка/обещание от колл-менеджера (созвон 20.08): консультант может быть
   * пуст — продажа никому не принадлежит, продавец проставится при конвертации.
   */
  allowEmptyConsultant?: boolean;
}) {
  const { activeConsultant } = useStore();
  const consultants = CONSULTANTS.filter((c) => c.role === "consultant").map((c) => c.name);
  const callManagers = CONSULTANTS.filter((c) => c.role === "callmanager").map((c) => c.name);

  return (
    <div className="grid sm:grid-cols-2 gap-4">
      <Field label="Консультант" required={!allowEmptyConsultant}>
        <select
          className="input"
          value={allowEmptyConsultant ? data.consultant : data.consultant || activeConsultant}
          onChange={(e) => onChange({ ...data, consultant: e.target.value })}
        >
          {allowEmptyConsultant && <option value="">— без консультанта —</option>}
          {consultants.map((c) => <option key={c}>{c}</option>)}
        </select>
        {allowEmptyConsultant && !data.consultant && (
          <p className="text-[12px] text-mute mt-1">
            Продажа не закреплена: продавец проставится при конвертации в продажу.
          </p>
        )}
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

/** Источник/цель обязательны только при проведении в «Успех». */
export function sourceMissingForSuccess(
  data: Pick<ClientData, "channel" | "purpose">,
  opts: { withPurpose?: boolean } = {}
): string[] {
  const withPurpose = opts.withPurpose !== false;
  const m: string[] = [];
  if (!data.channel) m.push("Источник рекламы");
  if (withPurpose && !data.purpose) m.push("На какой случай / цель");
  return m;
}

/** Канал и цель — выносятся ВНИЗ формы (после оплаты). */
export function SourceFields({
  data,
  onChange,
  withChannel = true,
  withPurpose = true,
  channelFixed,
  /** По умолчанию не обязательны — только при «Успех» (см. sourceMissingForSuccess). */
  required = false,
  /** Сумма чека до бонуса — для порога САР (созвон 20.08). Не задана — порог не проверяем. */
  checkTotal,
  /** Позиции чека: по костюмам считается, сколько САР положено (созвон 04.09). */
  items,
}: {
  data: ClientData;
  onChange: (d: ClientData) => void;
  withChannel?: boolean;
  withPurpose?: boolean;
  channelFixed?: string;
  required?: boolean;
  checkTotal?: number;
  items?: CartItem[];
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
          <Field label="Источник рекламы" required={required}>
            <select className="input" value={data.channel} onChange={(e) => set({ channel: e.target.value })}>
              <option value="">— выбрать —</option>
              {CHANNELS.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
        )
      )}
      {withPurpose && (
        <Field label="На какой случай / цель" required={required}>
          <select className="input" value={data.purpose} onChange={(e) => set({ purpose: e.target.value })}>
            <option value="">— выбрать —</option>
            {PURPOSES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </Field>
      )}
      {withChannel && (data.channel === SARAFAN_CHANNEL || channelFixed === SARAFAN_CHANNEL) && (
        <div className="sm:col-span-2">
          <SarafanField data={data} onChange={onChange} checkTotal={checkTotal} items={items} />
        </div>
      )}
    </div>
  );
}

const SARAFAN_CHANNEL = "Сарафан";

/**
 * Сарафан (п.3 правок 10.08; количество и метка «не найдено» — созвон 04.09).
 * Телефон друга проверяется в базе кассы и amoCRM, но не найден — не помеха:
 * САР всё равно уходит колл-менеджеру с меткой, он проверяет номер вручную.
 * Сколько костюмов в чеке — столько САР; костюмов нет, но чек не ниже порога —
 * одна. Бонус можно применить прямо в чеке: клиенту минус 1000 ₽ за каждую САР.
 */
function SarafanField({
  data,
  onChange,
  checkTotal,
  items,
}: {
  data: ClientData;
  onChange: (d: ClientData) => void;
  /** Сумма чека до вычета бонуса — сравнивается с порогом САР. */
  checkTotal?: number;
  items?: CartItem[];
}) {
  const { findByPhone } = useStore();
  const { saryMinCheck, sarySuitGroups } = useAppSettings();
  // Костюмов в чеке — столько САР положено (созвон 04.09). Костюм — это пара
  // пиджак плюс брюки одной вариации, размеры могут расходиться, поэтому счёт
  // берём с сервера: там же он считается при начислении. Пока ответа нет,
  // показываем прикидку по ветке каталога.
  const cartLines = useMemo(
    () =>
      (items ?? [])
        .filter((item) => !item.isReturn && item.qty > 0 && item.productId)
        .map((item) => ({ productId: item.productId, qty: item.qty })),
    [items]
  );
  const [serverSuits, setServerSuits] = useState<number | null>(null);
  useEffect(() => {
    if (USE_MOCK || cartLines.length === 0) {
      setServerSuits(null);
      return;
    }
    let alive = true;
    const t = setTimeout(() => {
      api
        .post<{ suits: number }>("/suits/cart-check", { items: cartLines })
        .then((res) => {
          if (alive) setServerSuits(res.suits ?? 0);
        })
        .catch(() => {
          if (alive) setServerSuits(null);
        });
    }, 400);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [cartLines]);
  const suitCountFallback = (items ?? []).reduce(
    (sum, item) =>
      !item.isReturn && isSuitCategory(item.category, sarySuitGroups)
        ? sum + Math.max(0, item.qty || 0)
        : sum,
    0
  );
  const suitCount = serverSuits ?? suitCountFallback;
  // Порог по сумме чека нужен, только когда костюмов в чеке нет (созвон 04.09).
  const belowThreshold =
    suitCount === 0 && checkTotal != null && checkTotal < saryMinCheck;
  const maxBonuses = belowThreshold ? 0 : Math.max(1, suitCount);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const lookupRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;

  const phone = data.saryPhone ?? "";
  const digits = phone.replace(/\D/g, "");
  const ownDigits = data.phone.replace(/\D/g, "");
  const selfReferral = digits.length >= 10 && digits === ownDigits;
  const found = Boolean(data.saryClient);
  const bonusCount = Math.round((data.saryBonus ?? 0) / SARY_BONUS);
  const bonusUsed = bonusCount > 0;

  // Чек ужали ниже порога или убрали костюмы — лишние бонусы снимаем сами.
  useEffect(() => {
    if (bonusCount > maxBonuses) {
      const next = maxBonuses * SARY_BONUS;
      onChange({ ...dataRef.current, saryBonus: next > 0 ? next : undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxBonuses, bonusCount]);

  function setBonusCount(next: number) {
    const clamped = Math.max(0, Math.min(next, maxBonuses));
    onChange({ ...dataRef.current, saryBonus: clamped > 0 ? clamped * SARY_BONUS : undefined });
  }

  function handlePhone(raw: string) {
    const next = formatPhone(raw);
    const nextDigits = next.replace(/\D/g, "");
    setChecked(false);
    // Телефон изменили — найденный друг и бонус больше не относятся к нему.
    onChange({ ...dataRef.current, saryPhone: next, saryClient: undefined, saryBonus: undefined });
    if (lookupRef.current) clearTimeout(lookupRef.current);
    if (nextDigits.length < 10 || nextDigits === ownDigits) {
      setChecking(false);
      return;
    }
    const local = findByPhone(next);
    if (local) {
      setChecked(true);
      onChange({ ...dataRef.current, saryPhone: next, saryClient: local.clientName, saryBonus: undefined });
      return;
    }
    if (USE_MOCK) return;
    lookupRef.current = setTimeout(async () => {
      setChecking(true);
      try {
        const res = await api.get<{ id: number; name: string }>(
          `/contacts/by-phone?phone=${encodeURIComponent(nextDigits.slice(0, 11))}`
        );
        onChange({
          ...dataRef.current,
          saryPhone: next,
          saryClient: res?.name || undefined,
          saryBonus: undefined,
        });
      } catch {
        onChange({ ...dataRef.current, saryPhone: next, saryClient: undefined, saryBonus: undefined });
      } finally {
        setChecked(true);
        setChecking(false);
      }
    }, 450);
  }

  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="text-[13px] font-semibold text-white">Сарафан: кто направил</div>
      <div className="grid sm:grid-cols-2 gap-4 items-start">
        <Field label="Телефон друга">
          <input
            className="input"
            inputMode="tel"
            placeholder="+7 (___) ___-__-__"
            value={phone}
            onChange={(e) => handlePhone(e.target.value)}
          />
          {selfReferral && (
            <div className="mt-1 text-[12px] text-amber-300/90">
              Это телефон самого клиента — бонус по сарафану не начисляется
            </div>
          )}
          {!selfReferral && checking && <div className="mt-1 text-[12px] text-mute">Проверяем в базе…</div>}
          {!selfReferral && !checking && found && (
            <div className="mt-1 inline-flex items-center gap-1.5 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-2 py-0.5">
              <CheckCircle2 size={12} /> Найден в базе: {data.saryClient}
            </div>
          )}
          {!selfReferral && !checking && checked && !found && (
            <div className="mt-1 inline-flex items-center gap-1.5 text-[12px] text-amber-300/90 bg-amber-400/10 border border-amber-400/20 rounded px-2 py-0.5">
              В базе не найден — САР уйдёт с меткой «не найдено», номер проверит колл-менеджер
            </div>
          )}
        </Field>
        <div className="pt-[22px]">
          <div className="flex items-center gap-2">
            <Button
              variant={bonusUsed ? "primary" : "subtle"}
              disabled={selfReferral || belowThreshold}
              onClick={() => setBonusCount(bonusUsed ? 0 : 1)}
            >
              {bonusUsed
                ? `Бонус применён −${money(bonusCount * SARY_BONUS)}`
                : `Использовать бонус −${money(SARY_BONUS)}`}
            </Button>
            {maxBonuses > 1 && (
              <div className="inline-flex items-center gap-1">
                <button
                  type="button"
                  className="chip bg-ink-700 text-mute-soft hover:text-white disabled:opacity-40"
                  disabled={bonusCount === 0}
                  onClick={() => setBonusCount(bonusCount - 1)}
                >
                  −
                </button>
                <span className="text-[12px] text-mute-soft w-[46px] text-center">
                  {bonusCount} / {maxBonuses}
                </span>
                <button
                  type="button"
                  className="chip bg-ink-700 text-mute-soft hover:text-white disabled:opacity-40"
                  disabled={bonusCount >= maxBonuses}
                  onClick={() => setBonusCount(bonusCount + 1)}
                >
                  +
                </button>
              </div>
            )}
          </div>
          <p className="hint-only text-[12px] text-mute mt-1">
            {belowThreshold
              ? `Костюмов в чеке нет, поэтому нужен чек от ${money(saryMinCheck)} — сейчас ${money(checkTotal ?? 0)}.`
              : suitCount > 1
                ? `Костюмов в чеке ${suitCount} — столько же САР по ${money(SARY_BONUS)}. В чеке учтено ${bonusCount}, остальные уйдут колл-менеджеру на перевод.`
                : bonusUsed
                  ? `Бонус −${money(SARY_BONUS)} в чеке — отдельный перевод другу не нужен.`
                  : `Без бонуса в чеке: при «Успех» колл-менеджеру — задача перевести ${money(SARY_BONUS)} на этот номер.`}
          </p>
        </div>
      </div>
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
 * Общая стоимость (до скидок) · Скидка итого (позиции + чек) · К оплате.
 * `subtotal` — сумма после скидок по позициям (+ доставка и т.п.).
 * `itemsDiscount` — сумма скидок по позициям (₽), прибавляется в плитку «Скидка».
 */
export function TotalsBlock({
  subtotal,
  state,
  onChange,
  itemsDiscount = 0,
  delivery = 0,
  saryBonus = 0,
}: {
  subtotal: number;
  state: DiscountState;
  onChange: (s: DiscountState) => void;
  /** Скидки по позициям (уже вычтены из subtotal) — показываем в общей «Скидке». */
  itemsDiscount?: number;
  /** Доставка прибавляется после скидки и не участвует в её расчёте. */
  delivery?: number;
  /** Бонус сарафана — отдельной строкой, не смешивается со скидкой. */
  saryBonus?: number;
}) {
  const checkDiscount = calcDiscount(subtotal, state);
  const totalDiscount = Math.max(0, itemsDiscount) + checkDiscount;
  const gross = subtotal + Math.max(0, itemsDiscount) + delivery;
  const total = Math.max(0, Math.max(0, subtotal - checkDiscount) + delivery - saryBonus);
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
        <SumTile label="Общая стоимость" value={money(gross)} />
        <SumTile
          label={
            state.discPct && checkDiscount > 0 && itemsDiscount > 0
              ? `Скидка (+${state.discPct}% на чек)`
              : state.discPct && checkDiscount > 0
                ? `Скидка ${state.discPct}%`
                : "Скидка"
          }
          value={totalDiscount > 0 ? `−${money(totalDiscount)}` : money(0)}
          tone="discount"
        />
        <SumTile label={delivery > 0 ? `К оплате · доставка ${money(delivery)}` : "К оплате"} value={money(total)} tone="strong" />
      </div>
      {saryBonus > 0 && (
        <div className="text-[12px] text-emerald-300/90">
          Бонус за сарафан −{money(saryBonus)} учтён в сумме к оплате
        </div>
      )}
    </div>
  );
}

// ── Sticky-строка: К оплате · Оплачено · Остаток/Сдача (компактная) ──────

export function SummaryBar({ total, paid }: { total: number; paid: number }) {
  const remainder = Math.max(0, total - paid);
  const change = Math.max(0, paid - total);
  const thirdLabel = change > 0 ? "Сдача" : "Остаток";
  const thirdValue = change > 0 ? moneyPlain(change) : moneyPlain(remainder);
  const thirdClass = change > 0 ? "text-amber-300" : remainder > 0 ? "text-amber-300" : "text-emerald-300";

  const cell = (label: string, value: string, valueClass: string) => (
    <div className="flex-1 min-w-0 text-center px-1">
      <div className="text-[9px] sm:text-[11px] md:text-xs uppercase tracking-wide text-mute mb-0.5 md:mb-1 leading-none">
        {label}
      </div>
      <div className={`text-[15px] sm:text-lg md:text-xl font-extrabold tabular-nums leading-none ${valueClass}`}>
        {value}
      </div>
    </div>
  );
  return (
    <div className="flex items-center justify-between gap-1 sm:gap-3 rounded-lg bg-ink-900 border border-ink-600 px-2.5 py-2 sm:px-4 sm:py-3 shadow-[0_-2px_16px_rgba(0,0,0,0.3)]">
      {cell("К оплате", moneyPlain(total), "text-white")}
      {cell("Оплачено", moneyPlain(paid), "text-white")}
      {cell(thirdLabel, thirdValue, thirdClass)}
    </div>
  );
}

// ── Сдача → Эдвин ─────────────────────────────────────────────────────

export interface ChangeInfo {
  status: "issued" | "pending";
  destination: string;
  /** Чем консультант выдал сдачу сам — заполняется только при статусе «Выдано клиенту». */
  methodId?: string;
  /** Раскладка выдачи по способам: часть налом, часть переводом, часть — через Эдвина. */
  payouts?: Payout[];
}

/**
 * Показывается когда paid > total (есть сдача). Сумма раскладывается по способам:
 * наличные из ящика, перевод с карты консультанта, строка «Переведёт Mansband»
 * (уходит в очередь Эдвина). Любую строку можно править — остаток уходит в парную.
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
      <div className="text-[13px] font-semibold text-white">Сдача</div>
      <PayoutRows
        total={change}
        rows={info.payouts}
        onChange={(payouts) => onChange({ ...info, payouts })}
        label=""
        destination={info.destination}
        onDestination={(destination) => onChange({ ...info, destination })}
      />
    </div>
  );
}

/**
 * Поля заявки по сдаче. Статус «pending» означает, что часть суммы переводит
 * Mansband — только эта часть попадает в очередь Эдвина.
 */
export function changeDealFields(change: number, info: ChangeInfo): Partial<Deal> {
  if (change <= 0) return {};
  const payouts = normalizePayouts(info.payouts, change);
  const viaMansband = mansbandPayoutAmount(payouts);
  const self = selfPayouts(payouts);
  return {
    changePayouts: payouts,
    changeStatus: viaMansband > 0 ? "pending" : "issued",
    changeDestination: viaMansband > 0 ? info.destination : undefined,
    changeMethodId: self[0]?.methodId,
  };
}

/** Поля заявки по чаевым. `cap` — сдача, больше неё чаевых быть не может. */
export function tipsDealFields(info: TipsInfo, cap: number): Partial<Deal> {
  const amount = Math.min(info.amount || 0, Math.max(0, cap));
  if (amount <= 0) return { tips: undefined };
  const payouts = normalizePayouts(info.payouts, amount);
  const viaMansband = mansbandPayoutAmount(payouts);
  const self = selfPayouts(payouts);
  return {
    tips: amount,
    tipsPayouts: payouts,
    tipsDestination: viaMansband > 0 ? info.destination || undefined : undefined,
    tipsMethodId: self[0]?.methodId,
  };
}

/** Поля заявки по возврату денег клиенту. */
export function returnDealFields(amount: number, info: ReturnInfo): Partial<Deal> {
  if (amount <= 0) return {};
  const payouts = normalizePayouts(info.payouts, amount);
  const viaMansband = mansbandPayoutAmount(payouts);
  return {
    returnPayouts: payouts,
    returnStatus: viaMansband > 0 ? "pending" : "issued",
    returnDestination: viaMansband > 0 ? info.destination : undefined,
  };
}

/** Есть ли строки выплаты с суммой без выбранного способа. */
export function payoutMethodsIncomplete(rows: Payout[] | undefined, total: number): boolean {
  if (total <= 0) return false;
  if (!rows || rows.length === 0) return true;
  return rows.some((r) => r.amount > 0 && !String(r.methodId ?? "").trim());
}

/**
 * Обязательные поля сдачи/чаевых: способ выдачи должен быть выбран
 * (не «— выбрать —»), иначе в ledger уйдёт пустой methodId.
 */
export function changeTipsMissing(
  paid: number,
  total: number,
  changeInfo: ChangeInfo,
  tips: TipsInfo
): string[] {
  const change = Math.max(0, paid - total);
  if (change <= 0) return [];
  const tipsAmt = Math.min(Math.max(0, tips.amount || 0), change);
  const toClient = Math.max(0, change - tipsAmt);
  const out: string[] = [];
  if (toClient > 0 && payoutMethodsIncomplete(changeInfo.payouts, toClient)) {
    out.push("Способ выдачи сдачи");
  }
  if (tipsAmt > 0 && payoutMethodsIncomplete(tips.payouts, tipsAmt)) {
    out.push("Способ выдачи чаевых");
  }
  if (toClient > 0) {
    const payouts = normalizePayouts(changeInfo.payouts, toClient);
    if (mansbandPayoutAmount(payouts) > 0 && !changeInfo.destination.trim()) {
      out.push("Куда переводить сдачу");
    }
  }
  return out;
}

/** Обязательный выбор способа при возврате клиенту. */
export function returnPayoutMissing(amount: number, info: ReturnInfo): string[] {
  if (amount <= 0) return [];
  const out: string[] = [];
  if (payoutMethodsIncomplete(info.payouts, amount)) out.push("Способ выдачи возврата");
  const payouts = normalizePayouts(info.payouts, amount);
  if (mansbandPayoutAmount(payouts) > 0 && !info.destination.trim()) {
    out.push("Реквизиты возврата");
  }
  return out;
}

// ── Чаевые ─────────────────────────────────────────────────────────────

export interface TipsInfo {
  amount: number;
  status: "issued" | "pending";
  destination: string;
  /** Чем консультант забрал чаевые сам — заполняется только при статусе «Забрал». */
  methodId?: string;
  /** Раскладка чаевых по способам, включая строку «Переведёт Mansband». */
  payouts?: Payout[];
}

/**
 * Чаевые из сдачи. Консультант указывает сумму и способ
 * (в т.ч. «Перевести Mansband» → очередь Эдвина).
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
  return (
    <div className="rounded-lg border border-ink-700 p-4 space-y-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
        <Coins size={15} className="text-gold" /> Чаевые
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
          вся сдача ({money(change)})
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
        <PayoutRows
          total={info.amount}
          rows={info.payouts}
          onChange={(payouts) =>
            onChange({
              ...info,
              payouts,
              // Реквизиты для Эдвина — ответственный за заявку, вводить нечего.
              destination:
                mansbandPayoutAmount(payouts) > 0
                  ? info.destination || responsible || ""
                  : info.destination,
            })
          }
          label=""
          destination={info.destination || responsible || ""}
          onDestination={(destination) => onChange({ ...info, destination })}
          destinationLabel="Кому перевести чаевые"
          destinationReadOnly
        />
      )}
    </div>
  );
}

// ── Дата оплаты ──────────────────────────────────────────────────────

/**
 * Единый блок оплаты для всех форм: способы → список с датой → сдача → чаевые.
 * Используй везде, где есть PaymentBlock, чтобы сдача/чаевые были одинаковыми.
 */
export function PaymentSection({
  total,
  payments,
  onPayments,
  consultant,
  changeInfo,
  onChangeInfo,
  tips,
  onTips,
}: {
  total: number;
  payments: Payment[];
  onPayments: (p: Payment[]) => void;
  consultant?: string;
  changeInfo: ChangeInfo;
  onChangeInfo: (i: ChangeInfo) => void;
  tips: TipsInfo;
  onTips: (t: TipsInfo) => void;
}) {
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  const change = Math.max(0, paid - total);
  // Чаевые вычитаются из сдачи; клиенту уходит остаток.
  const toClient = Math.max(0, change - Math.min(tips.amount || 0, change));
  const changeBlockRef = useRef<HTMLDivElement>(null);
  const prevChange = useRef(0);
  useEffect(() => {
    if (change > 0 && prevChange.current <= 0) {
      changeBlockRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    prevChange.current = change;
  }, [change]);
  return (
    <div className="space-y-4">
      <PaymentBlock total={total} payments={payments} onChange={onPayments} />
      {change > 0 && (
        <div
          ref={changeBlockRef}
          className="rounded-xl border border-ink-700 p-3 sm:p-4 space-y-4"
        >
          <div className="text-[13px] font-semibold text-white tracking-wide">
            СДАЧА И ЧАЕВЫЕ
          </div>
          <ChangeBlock change={toClient} info={changeInfo} onChange={onChangeInfo} />
          <TipsBlock change={change} info={tips} onChange={onTips} responsible={consultant} />
        </div>
      )}
    </div>
  );
}

/** Дата последней оплаты из списка (для writeback / deal.paymentDate). */
export function lastPaymentDate(payments: Payment[]): string | undefined {
  for (let i = payments.length - 1; i >= 0; i--) {
    if (payments[i]?.paidAt) return payments[i]!.paidAt;
  }
  return undefined;
}

// ── Доплата (вторая оплата на остаток) ───────────────────────────────

export interface TopUpInfo {
  open: boolean;
  payments: Payment[];
  changeInfo: ChangeInfo;
  tips: TipsInfo;
}

export const emptyTopUp: TopUpInfo = {
  open: false,
  payments: [],
  changeInfo: { status: "issued", destination: "" },
  tips: { amount: 0, status: "issued", destination: "" },
};

/**
 * Блок «Доплата»: кнопка раскрывает вторую оплату на остаток.
 * Для Аренды / Отложки / Обмена — когда клиент доплачивает позже.
 */
export function TopUpBlock({
  remainder,
  info,
  onChange,
  consultant,
}: {
  remainder: number;
  info: TopUpInfo;
  onChange: (i: TopUpInfo) => void;
  consultant?: string;
}) {
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
      <PaymentSection
        total={Math.max(0, remainder)}
        payments={info.payments}
        onPayments={(p) => onChange({ ...info, payments: p })}
        consultant={consultant}
        changeInfo={info.changeInfo}
        onChangeInfo={(ci) => onChange({ ...info, changeInfo: ci })}
        tips={info.tips}
        onTips={(t) => onChange({ ...info, tips: t })}
      />
    </div>
  );
}

// ── Возврат денег → Эдвин ────────────────────────────────────────────

export interface ReturnInfo {
  status: "issued" | "pending";
  destination: string;
  /** Раскладка возврата по способам, включая строку «Переведёт Mansband». */
  payouts?: Payout[];
}

/**
 * Возврат денег клиенту. Как и сдача, раскладывается по способам частями;
 * строка «Перевести Mansband» уходит в очередь Эдвина (задача «Сделать возврат»).
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
      <p className="text-[12px] text-mute">
        Выберите способ: наличные / карта консультанта — сразу; «Перевести Mansband» — задача Эдвину
        «Сделать возврат».
      </p>
      <PayoutRows
        total={amount}
        rows={info.payouts}
        onChange={(payouts) => onChange({ ...info, payouts })}
        label="Способ возврата"
        destination={info.destination}
        onDestination={(destination) => onChange({ ...info, destination })}
        destinationLabel="Куда вернуть"
        destinationHint="Обязательно — иначе Эдвин не знает, куда переводить"
      />
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
  successStage?: string;
  successDisabled?: boolean;
  successHint?: string;
  onSuccess?: () => void;
}) {
  // «Новая заявка» / «Взято в работу» в селекте не показываем, если есть другие этапы.
  // Если все этапы скрыты — оставляем как есть (форма сама должна дать видимый список).
  const visibleStages = stages.filter((s) => !HIDDEN_STAGES.has(s));
  const selectStages =
    visibleStages.length === 0
      ? stages
      : visibleStages.includes(stage) || HIDDEN_STAGES.has(stage)
        ? visibleStages
        : [stage, ...visibleStages];
  const effectiveStage =
    HIDDEN_STAGES.has(stage) && visibleStages.length > 0
      ? (selectStages[0] ?? stage)
      : stage;

  return (
    <div className="flex items-center gap-2 sm:gap-3 justify-end flex-wrap py-0.5">
      {successHint && (
        <span className="text-[11px] sm:text-[12px] text-amber-300/90 mr-auto w-full sm:w-auto">
          {successHint}
        </span>
      )}
      <select
        className="input w-auto text-sm py-2"
        value={effectiveStage}
        onChange={(e) => onStageChange(e.target.value)}
        disabled={saved}
      >
        {selectStages.map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <Button variant="subtle" className="py-2" disabled={disabled || saved} onClick={() => onSave(effectiveStage)}>
        Сохранить
      </Button>
      {successStage && onSuccess && (
        <Button className="py-2" disabled={successDisabled || saved} onClick={onSuccess}>
          Успех
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
  missingRequired,
  stageBadge,
  headerActions,
}: {
  title: string;
  subtitle?: string;
  onBack: () => void;
  children: ReactNode;
  footer: ReactNode;
  meta?: { number: number; createdAt: string };
  storeAddress?: string;
  summary?: ReactNode;
  /** Незаполненные обязательные поля — внизу формы, не в sticky-баре. */
  missingRequired?: string[];
  /** Цветной статус справа в шапке. */
  stageBadge?: ReactNode;
  /** Кнопки над контентом (задача / история / перемещение). */
  headerActions?: ReactNode;
}) {
  const hasDock = !!(summary || footer);
  const missing = (missingRequired ?? []).filter(Boolean);
  return (
    <div
      className={`max-w-3xl mx-auto ${
        hasDock ? "pb-[calc(9.5rem+env(safe-area-inset-bottom))] sm:pb-36" : ""
      }`}
    >
      <button
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-mute hover:text-white text-sm mb-4"
      >
        <ArrowLeft size={16} /> Назад к выбору
      </button>
      <div className="mb-5 flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-extrabold text-white">{title}</h1>
            {meta && (
              <>
                <span className="chip bg-white/10 text-white font-mono text-[15px] font-semibold tracking-wide">
                  Заявка №{meta.number}
                </span>
                <span className="chip bg-white/10 text-white/90 font-mono">
                  {dateRu(meta.createdAt)}
                </span>
              </>
            )}
            {storeAddress && <StoreAddressChip address={storeAddress} />}
          </div>
          {subtitle && <p className="text-mute text-sm mt-0.5">{subtitle}</p>}
        </div>
        {stageBadge}
      </div>
      {headerActions && <div className="mb-5">{headerActions}</div>}
      <div className="space-y-5">{children}</div>

      {missing.length > 0 && (
        <div className="mt-5 rounded-lg border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-[13px] text-amber-100/95">
          <div className="font-semibold text-amber-200 mb-1">Не заполнены обязательные поля</div>
          <ul className="list-disc pl-4 space-y-0.5 text-amber-100/80">
            {missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Фиксированный нижний блок: сумма/оплата/остаток + этапы — всегда на экране */}
      {hasDock && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-700 bg-ink-950/95 backdrop-blur pb-[env(safe-area-inset-bottom)]">
          <div className="max-w-3xl mx-auto px-3 sm:px-4 pt-2.5 pb-2.5 space-y-2">
            {summary}
            {footer}
          </div>
        </div>
      )}
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
