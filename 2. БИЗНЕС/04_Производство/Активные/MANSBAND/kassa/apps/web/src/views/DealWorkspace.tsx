import { useEffect, useMemo, useState } from "react";
import { ArrowLeft } from "lucide-react";
import type { CartItem, Deal, Payment } from "../data/types";
import { useStore } from "../store";
import { useAuth } from "../auth/AuthContext";
import { canEditClosedDeals } from "../auth/roles";
import { KIND_LABEL } from "../lib/labels";
import { Button, Card, Field, Modal, StageBadge, Select, opts } from "../components/ui";
import { ProductPicker } from "../components/ProductPicker";
import { PaymentBlock } from "../components/PaymentBlock";
import { DealActionsBar } from "../components/DealActionsBar";
import { MovementModal, defaultMovementTarget } from "../components/MovementModal";
import { CreateTaskForm } from "../components/CreateTaskForm";
import { ConvertKindForm, type ConvertKindPayload } from "../components/ConvertKindForm";
import { RefundForm } from "./forms/RefundForm";
import { ExchangeForm } from "./forms/ExchangeForm";
import { HIDDEN_STAGES, STAGES_BY_KIND } from "../data/mock";
import { api, USE_MOCK } from "../api/client";
import { formatPhone, money } from "../lib/format";
import { Hint } from "../lib/hints";
import {
  ClientFields,
  CommentField,
  ConsultantFields,
  ReturnBlock,
  SectionTitle,
  SourceFields,
  SummaryBar,
  returnDealFields,
  returnPayoutMissing,
  sourceMissingForSuccess,
  type ClientData,
  type ConsultantData,
  type ReturnInfo,
} from "./forms/common";

function isClosed(deal: Deal): boolean {
  return deal.stage === "Успех" || deal.stage === "Провал";
}

function formatHistoryAction(action: string): string {
  return action
    .replace(/^Этап изменён:\s*/i, "Этап: ")
    .replace(/^Комментарий:\s*/i, "")
    .replace(/^Задача:\s*/i, "Задача · ")
    .replace(/^Перемещение\s*/i, "Перемещение · ")
    .replace(/^Выдача клиенту\s*[—-]\s*/i, "Выдача · ")
    .replace(/^Продажа компании\s*[—-]\s*/i, "Компания · ")
    .replace(/^Заявка обновлена\s*/i, "Обновление");
}

/** Снимок полей карточки. Совпадает у только что открытой сделки и у формы без правок. */
function formFromDeal(live: Deal): {
  consultants: ConsultantData;
  client: ClientData;
  items: CartItem[];
  payments: Deal["payments"];
  comment: string;
  stage: string;
  stageReason: string;
  companyName: string;
  managerName: string;
  managerPhone: string;
  atelier: string;
  deliveryAmount: string;
  issued: boolean;
  rentalFrom: string;
  rentalTo: string;
  returnInfo: ReturnInfo;
} {
  return {
    consultants: { consultant: live.consultant, referredBy: live.referredBy || "" },
    client: {
      name: live.clientName,
      phone: live.clientPhone,
      channel: live.channel || "",
      purpose: live.purpose || "",
      saryPhone: live.saryPhone,
      saryClient: live.saryClient,
      saryBonus: live.saryBonus,
    },
    items: live.items.map((it) => ({ ...it, itemStatus: it.itemStatus ?? "waiting" })),
    payments: live.payments,
    comment: live.comment || "",
    stage: live.stage,
    stageReason: "",
    companyName: live.companyName || "",
    managerName: live.managerName || "",
    managerPhone: live.managerPhone || "",
    atelier: live.atelierAmount ? String(live.atelierAmount) : "",
    deliveryAmount: live.deliveryAmount ? String(live.deliveryAmount) : "",
    issued: Boolean(live.issued),
    rentalFrom: live.rentalFrom || "",
    rentalTo: live.rentalTo || "",
    returnInfo: {
      status: live.returnStatus ?? "issued",
      destination: live.returnDestination ?? "",
      payouts: live.returnPayouts,
    } satisfies ReturnInfo,
  };
}

function formSnapshot(form: ReturnType<typeof formFromDeal>): string {
  return JSON.stringify({
    ...form,
    items: form.items.map(({ suitGroupId: _g, suitPriceApplied: _a, suitOriginalPrice: _o, ...rest }) => rest),
  });
}

/**
 * Открытая заявка в том же каркасе, что и создание новой:
 * статус справа сверху, действия, консультант/клиент, товары, оплаты.
 */
export function DealWorkspace({
  deal,
  onClose,
  onReturnExchange: _onReturnExchange,
  backLabel = "К заявкам",
  hideBack = false,
  /** Шапка окна с номером заявки: туда уезжает статус, чтобы стоять на той же строке. */
  onDisplayStage,
}: {
  deal: Deal;
  onClose: () => void;
  onReturnExchange?: (kind: "refund" | "exchange", dealNumber: number) => void;
  /** Подпись кнопки назад — из задач: «К задачам». */
  backLabel?: string;
  /** В модалке крестик уже есть — кнопку «назад» не дублируем. */
  hideBack?: boolean;
  onDisplayStage?: (stage: string) => void;
}) {
  const { deals, updateDeal, replaceDeal, addDealComment, activeConsultant } = useStore();
  const { user } = useAuth();
  const live = deals.find((d) => d.id === deal.id || d.number === deal.number) ?? deal;

  const closed = isClosed(live);
  const canEdit = !closed || canEditClosedDeals(user?.role, user?.login, user?.name);
  const readOnly = !canEdit;

  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: live.consultant,
    referredBy: live.referredBy || "",
  });
  const [client, setClient] = useState<ClientData>({
    name: live.clientName,
    phone: live.clientPhone,
    channel: live.channel || "",
    purpose: live.purpose || "",
    saryPhone: live.saryPhone,
    saryClient: live.saryClient,
    saryBonus: live.saryBonus,
  });
  const [items, setItems] = useState<CartItem[]>(
    live.items.map((it) => ({ ...it, itemStatus: it.itemStatus ?? "waiting" }))
  );
  const [payments, setPayments] = useState<Payment[]>(live.payments);
  const [comment, setComment] = useState(live.comment || "");
  const [stage, setStage] = useState(live.stage);
  useEffect(() => {
    if (hideBack) onDisplayStage?.(stage);
  }, [hideBack, onDisplayStage, stage]);
  const [stageReason, setStageReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [taskOpen, setTaskOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [convertOpen, setConvertOpen] = useState(false);
  const [returnMode, setReturnMode] = useState<null | "refund" | "exchange">(null);
  const [converting, setConverting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [companyName, setCompanyName] = useState(live.companyName || "");
  const [managerName, setManagerName] = useState(live.managerName || "");
  const [managerPhone, setManagerPhone] = useState(live.managerPhone || "");
  const [atelier, setAtelier] = useState(live.atelierAmount ? String(live.atelierAmount) : "");
  const [deliveryAmount, setDeliveryAmount] = useState(
    live.deliveryAmount ? String(live.deliveryAmount) : ""
  );
  const [issued, setIssued] = useState(Boolean(live.issued));
  const [rentalFrom, setRentalFrom] = useState(live.rentalFrom || "");
  const [rentalTo, setRentalTo] = useState(live.rentalTo || "");
  const [returnInfo, setReturnInfo] = useState<ReturnInfo>({
    status: live.returnStatus ?? "issued",
    destination: live.returnDestination ?? "",
    payouts: live.returnPayouts,
  });
  const [baseline, setBaseline] = useState(() => formSnapshot(formFromDeal(live)));

  // Расположение позиций (П3, созвон 20.08): где физически лежит каждая позиция.
  const [itemStates, setItemStates] = useState<
    Array<{ itemId: string; state: string; location: string | null }>
  >([]);
  useEffect(() => {
    if (USE_MOCK || !live.number) return;
    let alive = true;
    api
      .get<Array<{ itemId: string; state: string; location: string | null }>>(
        `/deals/${live.number}/item-state`
      )
      .then((rows) => {
        if (alive) setItemStates(rows);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [live.number]);

  useEffect(() => {
    const form = formFromDeal(live);
    setConsultants(form.consultants);
    setClient(form.client);
    setItems(form.items);
    setPayments(form.payments);
    setComment(form.comment);
    setStage(form.stage);
    setStageReason(form.stageReason);
    setCompanyName(form.companyName);
    setManagerName(form.managerName);
    setManagerPhone(form.managerPhone);
    setAtelier(form.atelier);
    setDeliveryAmount(form.deliveryAmount);
    setIssued(form.issued);
    setRentalFrom(form.rentalFrom);
    setRentalTo(form.rentalTo);
    setReturnInfo(form.returnInfo);
    setBaseline(formSnapshot(form));
  }, [live.id, live.number, live.kind]);

  const dirty =
    formSnapshot({
      consultants,
      client,
      items,
      payments,
      comment,
      stage,
      stageReason,
      companyName,
      managerName,
      managerPhone,
      atelier,
      deliveryAmount,
      issued,
      rentalFrom,
      rentalTo,
      returnInfo,
    }) !== baseline;

  const total = useMemo(
    () => items.reduce((s, it) => s + (it.noPrice || it.isGift ? 0 : it.price * it.qty), 0),
    [items]
  );
  const paid = payments.reduce((s, p) => s + p.amount, 0);
  /** Сумма к возврату клиенту: чистый возврат или обмен «мы должны». */
  const refundAmount = useMemo(() => {
    if (live.kind === "refund") return Math.max(0, Math.abs(live.total || total || 0));
    if (live.kind === "exchange" && (live.total || 0) < 0) return Math.abs(live.total);
    return 0;
  }, [live.kind, live.total, total]);
  const holdKind = live.kind === "deferred" || live.kind === "promise";
  const stages = (STAGES_BY_KIND[live.kind] ?? [])
    .filter((s) => !HIDDEN_STAGES.has(s))
    .filter((s) => !(holdKind && s === "Успех"))
    .filter((s) => !(live.kind === "sliv" && !live.meetingDate && s !== "Провал"));
  const stageOptions = stages.includes(stage) || HIDDEN_STAGES.has(stage) ? stages : [stage, ...stages];
  const history = [...(live.history ?? [])].reverse();
  const closedNeedsReason = closed && canEdit && stage !== live.stage;

  async function saveAll() {
    if (readOnly) return;
    if (closedNeedsReason && !stageReason.trim()) {
      setError("Укажите причину изменения завершённой заявки");
      return;
    }
    if (holdKind && stage === "Успех" && live.stage !== "Успех") {
      setError("Отложку и обещание нельзя закрыть в Успех. Сначала смените тип заявки сверху.");
      return;
    }
    if (stage === "Успех" && live.stage !== "Успех" && (live.kind === "sale" || live.kind === "company" || live.kind === "rental")) {
      const due = live.total || total;
      const gaps = [
        paid < due && `оплата, остаток ${(due - paid).toLocaleString("ru-RU")} ₽`,
        ...sourceMissingForSuccess(client),
        live.kind === "company" && !issued && "фактическая выдача",
        live.kind === "rental" && !rentalFrom && "дата начала аренды",
        live.kind === "rental" && !rentalTo && "дата конца аренды",
      ].filter(Boolean) as string[];
      if (gaps.length) {
        setError(`Для Успех не хватает: ${gaps.join(", ")}`);
        return;
      }
    }
    if (refundAmount > 0) {
      const missing = returnPayoutMissing(refundAmount, returnInfo);
      if (missing.length) {
        setError(missing[0] ?? "Укажите способ возврата");
        return;
      }
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await updateDeal(live.id, {
        consultant: consultants.consultant,
        referredBy: consultants.referredBy || undefined,
        clientName: client.name,
        clientPhone: client.phone,
        channel: client.channel || undefined,
        purpose: client.purpose || undefined,
        saryPhone: client.saryPhone,
        saryClient: client.saryClient,
        saryBonus: client.saryBonus,
        items,
        payments,
        comment: comment || undefined,
        stage,
        reason: stageReason.trim() || undefined,
        total: live.total || total,
        paid,
        ...(live.kind === "company"
          ? {
              companyName: companyName.trim() || undefined,
              managerName: managerName.trim() || undefined,
              managerPhone: managerPhone.trim() || undefined,
              atelierAmount: Number(atelier) || undefined,
              deliveryAmount: Number(deliveryAmount) || undefined,
              issued,
            }
          : {}),
        ...(live.kind === "rental"
          ? {
              rentalFrom: rentalFrom || undefined,
              rentalTo: rentalTo || undefined,
            }
          : {}),
        ...(refundAmount > 0 ? returnDealFields(refundAmount, returnInfo) : {}),
      });
      if (!saved) {
        setError("Не удалось сохранить — проверьте права или сеть");
        return;
      }
      setBaseline(
        formSnapshot({
          consultants,
          client,
          items,
          payments,
          comment,
          stage,
          stageReason,
          companyName,
          managerName,
          managerPhone,
          atelier,
          deliveryAmount,
          issued,
          rentalFrom,
          rentalTo,
          returnInfo,
        })
      );
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 1500);
    } finally {
      setSaving(false);
    }
  }

  const canReturnExchange = closed && live.kind !== "refund" && live.kind !== "exchange";
  const linkedReturns = deals.filter(
    (d) => d.linkedDealNumber === live.number && (d.kind === "refund" || d.kind === "exchange")
  );

  // Отложку/обещание проводят как продажу, компанию или аренду: тот же номер
  // заявки и все данные (правки владельца 10.08.2026, п.1.1).
  const canConvert = !readOnly && (live.kind === "deferred" || live.kind === "promise");

  async function convertKind(payload: ConvertKindPayload) {
    setConverting(true);
    setError(null);
    try {
      if (USE_MOCK) {
        const saved = await updateDeal(live.id, { kind: payload.kind, ...payload } as Partial<Deal>);
        if (saved) replaceDeal(saved);
      } else {
        const updated = await api.patch<Deal>(`/deals/${live.number}/kind`, payload);
        if (updated) replaceDeal(updated);
      }
      setConvertOpen(false);
      // Уходим из «Отложка»/«Обещания» в раздел нового типа, карточка остаётся открытой.
      const next = `board/${payload.kind}/all/${live.number}`;
      if (window.location.hash.replace(/^#/, "") !== next) {
        window.location.hash = next;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сменить тип заявки");
      throw err;
    } finally {
      setConverting(false);
    }
  }

  return (
    <div
      className={`${hideBack ? "" : "max-w-3xl mx-auto"} pb-[calc(9.5rem+env(safe-area-inset-bottom))] sm:pb-36`}
    >
      {!hideBack && (
        <button
          type="button"
          onClick={onClose}
          className="inline-flex items-center gap-1.5 text-mute hover:text-white text-sm mb-4"
        >
          <ArrowLeft size={16} /> {backLabel}
        </button>
      )}

      <div className="mb-1 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-extrabold text-white">{KIND_LABEL[live.kind] ?? live.kind}</h1>
            {canConvert && (
              <button
                type="button"
                onClick={() => setConvertOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/30 bg-white px-3 py-1.5 text-[13px] font-semibold text-ink-950 hover:bg-white/90"
              >
                Сменить тип
              </button>
            )}
            {!hideBack && (
              <span className="chip bg-white/10 text-white font-mono text-[15px] font-semibold tracking-wide">
                Заявка №{live.number}
              </span>
            )}
          </div>
          {readOnly && (
            <p className="text-mute text-sm mt-0.5">Только просмотр — менять может РОП или Максим</p>
          )}
        </div>
        {!hideBack && (
          <StageBadge stage={stage} className="text-[16px] px-3.5 py-2 font-semibold shrink-0 self-center" />
        )}
      </div>
      {!readOnly && (
        <Hint>
          Карточка заявки: клиент, товары, оплата и этап. Задачу, смену вида и историю — кнопки
          сверху. Перемещение из карточки доступно для отложки и обещания.
        </Hint>
      )}

      <div className="space-y-5">
        <DealActionsBar
          onTask={() => setTaskOpen(true)}
          onConvert={canConvert ? () => setConvertOpen(true) : undefined}
          onHistory={() => setHistoryOpen(true)}
          historyCount={history.length}
          onMovement={
            !readOnly && (live.kind === "deferred" || live.kind === "promise")
              ? () => setMoveOpen(true)
              : undefined
          }
        />

        <MovementModal
          open={moveOpen}
          onClose={() => setMoveOpen(false)}
          dealNumber={live.number}
          store={live.store || "На Бауманской"}
          items={items}
          // Заявка-зеркало из amoCRM приходит без состава: консультант выбирает
          // позиции из каталога прямо в перемещении (созвон 04.09).
          allowCatalogPick={items.length === 0}
          defaultTarget={defaultMovementTarget(live.store || "На Бауманской")}
          onCreated={(summary) => {
            void (async () => {
              addDealComment(live.id, `Перемещение ${summary}`, activeConsultant);
              setStage("Ждет товар");
              if (USE_MOCK) {
                await updateDeal(live.id, { stage: "Ждет товар" } as Partial<Deal>);
                return;
              }
              try {
                const updated = await api.get<Deal>(`/deals/${live.number}`);
                if (updated) {
                  replaceDeal(updated);
                  setStage(updated.stage || "Ждет товар");
                }
              } catch {
                // этап уже выставил API при создании задачи
              }
            })();
          }}
        />

        <Card>
          <SectionTitle>Консультант и клиент</SectionTitle>
          <fieldset disabled={readOnly} className="space-y-4 disabled:opacity-80">
            <ConsultantFields data={consultants} onChange={setConsultants} />
            <ClientFields data={client} onChange={setClient} />
          </fieldset>
        </Card>

        {live.kind === "company" && (
          <Card>
            <SectionTitle>Компания</SectionTitle>
            <fieldset disabled={readOnly} className="grid sm:grid-cols-2 gap-3 disabled:opacity-80">
              <Field label="Телефон руководителя" required>
                <input
                  className="input"
                  inputMode="tel"
                  value={managerPhone}
                  onChange={(e) => setManagerPhone(formatPhone(e.target.value))}
                />
              </Field>
              <Field label="Имя руководителя" required>
                <input
                  className="input"
                  value={managerName}
                  onChange={(e) => setManagerName(e.target.value.replace(/[0-9]/g, ""))}
                />
              </Field>
              <Field label="Наименование компании" required>
                <input
                  className="input"
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                />
              </Field>
              <Field label="Ателье, ₽">
                <input
                  className="input"
                  inputMode="numeric"
                  value={atelier}
                  onChange={(e) => setAtelier(e.target.value.replace(/\D/g, ""))}
                />
              </Field>
              <Field label="Доставка, ₽">
                <input
                  className="input"
                  inputMode="numeric"
                  value={deliveryAmount}
                  onChange={(e) => setDeliveryAmount(e.target.value.replace(/\D/g, ""))}
                />
              </Field>
              <label className="flex items-center gap-2 text-sm text-mute-soft sm:col-span-2">
                <input type="checkbox" checked={issued} onChange={(e) => setIssued(e.target.checked)} />
                Товар фактически выдан
              </label>
              {live.invoiceStatus && (
                <div className="text-[12px] text-mute sm:col-span-2">
                  Счёт: {live.invoiceStatus}
                  {live.invoiceNo ? ` · ${live.invoiceNo}` : " · ожидает Эдвина"}
                </div>
              )}
            </fieldset>
          </Card>
        )}

        {live.kind === "rental" && (
          <Card>
            <SectionTitle>Сроки аренды</SectionTitle>
            <fieldset disabled={readOnly} className="grid sm:grid-cols-2 gap-3 disabled:opacity-80">
              <Field label="Начало аренды">
                <input
                  className="input"
                  type="date"
                  value={rentalFrom}
                  onChange={(e) => setRentalFrom(e.target.value)}
                />
              </Field>
              <Field label="Конец аренды">
                <input
                  className="input"
                  type="date"
                  value={rentalTo}
                  onChange={(e) => setRentalTo(e.target.value)}
                />
              </Field>
            </fieldset>
          </Card>
        )}

        <Card>
          <SectionTitle>Товары</SectionTitle>
          <ProductPicker items={items} onChange={setItems} readOnly={readOnly} dealNumber={live.number} />
          {itemStates.length > 0 && (
            <div className="mt-3 border-t border-ink-800 pt-3">
              <div className="field-label mb-1.5">Расположение позиций</div>
              <div className="space-y-1">
                {items.map((it) => {
                  const st = itemStates.find((s) => s.itemId === it.productId);
                  if (!st) return null;
                  return (
                    <div key={it.productId} className="flex items-center gap-2 text-[13px]">
                      <span className="text-mute-soft flex-1 min-w-0 truncate">{it.name}</span>
                      <span className="chip bg-ink-700 text-white shrink-0">
                        {st.location || (st.state === "in_transit" ? "В пути" : "—")}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle>Оплата</SectionTitle>
          <fieldset disabled={readOnly} className="disabled:opacity-80">
            <PaymentBlock total={live.total || total} payments={payments} onChange={setPayments} />
          </fieldset>
        </Card>

        <Card>
          <SectionTitle>Источник и цель</SectionTitle>
          <fieldset disabled={readOnly} className="disabled:opacity-80">
            <SourceFields
              data={client}
              onChange={setClient}
              items={items}
              checkTotal={total + (client.saryBonus ?? 0)}
              withPurpose={
                live.kind === "promise" || live.kind === "cert_plastic" || live.kind === "cert_digital"
                  ? Boolean(live.purpose)
                  : true
              }
            />
          </fieldset>
        </Card>

        {refundAmount > 0 && (
          <Card>
            <SectionTitle>Возврат средств клиенту</SectionTitle>
            <fieldset disabled={readOnly} className="disabled:opacity-80">
              <ReturnBlock amount={refundAmount} info={returnInfo} onChange={setReturnInfo} />
            </fieldset>
          </Card>
        )}

        <Card>
          <SectionTitle>Комментарий</SectionTitle>
          <fieldset disabled={readOnly} className="disabled:opacity-80">
            <CommentField value={comment} onChange={setComment} />
          </fieldset>
        </Card>

        <Card>
          <SectionTitle>Этап заявки</SectionTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              size="sm"
              className="w-[240px]"
              value={stage}
              disabled={readOnly}
              onChange={setStage}
              options={opts(...stageOptions)}
            />
            {holdKind && (
              <span className="text-[12px] text-mute">
                В Успех отсюда не закрывается. Сначала «Сменить тип»: продажа, компания или аренда. Появятся поля этого вида, включая оплату.
              </span>
            )}
          </div>
          {closedNeedsReason && (
            <label className="block mt-3">
              <span className="field-label">Причина изменения</span>
              <input
                className="input"
                value={stageReason}
                onChange={(e) => setStageReason(e.target.value)}
                placeholder="Зачем меняем закрытую заявку"
              />
            </label>
          )}
          {readOnly && (
            <div className="mt-2 text-[12px] text-amber-300/90">
              Завершённые заявки может править только РОП или Максим.
            </div>
          )}
          {canReturnExchange && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="subtle" onClick={() => setReturnMode("refund")}>
                Возврат
              </Button>
              <Button variant="subtle" onClick={() => setReturnMode("exchange")}>
                Обмен
              </Button>
            </div>
          )}
          {linkedReturns.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {linkedReturns.map((d) => (
                <div key={d.id} className="flex items-center justify-between gap-3 text-[13px]">
                  <span className="text-white">
                    {KIND_LABEL[d.kind]} · заявка №{d.number}
                    <span className="text-mute"> · {d.stage}</span>
                  </span>
                  <span className="text-white font-semibold shrink-0">{money(d.total)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        {error && (
          <div className="rounded-lg border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}
      </div>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-700 bg-ink-950/95 backdrop-blur pb-[env(safe-area-inset-bottom)]">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 pt-2.5 pb-2.5 space-y-2">
          <SummaryBar total={live.total || total} paid={paid} />
          <div className="flex justify-end gap-2">
            <Button variant="subtle" onClick={onClose}>
              Закрыть
            </Button>
            {!readOnly && (dirty || saving || savedFlash) && (
              <Button disabled={saving} onClick={() => void saveAll()}>
                {savedFlash ? "Сохранено" : saving ? "Сохраняем…" : "Сохранить"}
              </Button>
            )}
          </div>
        </div>
      </div>

      <Modal open={taskOpen} onClose={() => setTaskOpen(false)} title={`Задача · #${live.number}`}>
        <CreateTaskForm
          defaultStore={live.store || "На Бауманской"}
          dealNumber={live.number}
          lockDealNumber
          includeMovement={false}
          simple
          compact
          onCreated={(task) => {
            addDealComment(
              live.id,
              `Задача: ${task.title} → ${task.assigneeName || task.assigneeRole}`,
              activeConsultant
            );
            setTaskOpen(false);
          }}
        />
      </Modal>

      <Modal
        open={convertOpen}
        onClose={() => !converting && setConvertOpen(false)}
        title={`Сменить тип заявки №${live.number}`}
      >
        <ConvertKindForm
          deal={live}
          busy={converting}
          onCancel={() => setConvertOpen(false)}
          onSubmit={async (payload) => {
            try {
              await convertKind(payload);
            } catch {
              // ошибка уже в setError
            }
          }}
        />
      </Modal>

      <Modal
        open={returnMode != null}
        onClose={() => setReturnMode(null)}
        title={returnMode === "exchange" ? "Обмен" : "Возврат"}
        xl
      >
        {returnMode === "refund" && (
          <RefundForm embedded sourceDeal={live} onDone={() => setReturnMode(null)} />
        )}
        {returnMode === "exchange" && (
          <ExchangeForm embedded sourceDeal={live} onDone={() => setReturnMode(null)} />
        )}
      </Modal>

      <Modal open={historyOpen} onClose={() => setHistoryOpen(false)} title={`История · #${live.number}`}>
        {history.length === 0 ? (
          <div className="py-8 text-center text-mute text-sm">Пока пусто</div>
        ) : (
          <div className="space-y-2 max-h-[60vh] overflow-y-auto">
            {history.map((h, i) => (
              <div key={`${h.at}-${i}`} className="rounded-lg border border-ink-700 px-3 py-2.5 text-[13px]">
                <div className="text-white leading-snug">{formatHistoryAction(h.action)}</div>
                <div className="text-[11px] text-mute mt-1">
                  {h.who} ·{" "}
                  {new Date(h.at).toLocaleString("ru-RU", {
                    day: "2-digit",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>

    </div>
  );
}
