import { useMemo, useState } from "react";
import { Gift, Check, Copy, Clock3, Plus, Search, BadgePercent } from "lucide-react";
import { useStore } from "../store";
import { money, timeOf, shortDate } from "../lib/format";
import { Button, Modal, StatTile } from "../components/ui";
import { PhotoField } from "./forms/common";
import { PaymentMethodSelect } from "../components/PaymentMethodSelect";
import { attachmentsToUpload, type PhotoAttachment } from "../lib/photo";
import type { SaryPayout } from "../data/types";
import { ApiError } from "../api/client";
import { Hint } from "../lib/hints";

/** Авто-текст сообщения клиенту о выплате «сары». */
function saryMessage(s: SaryPayout): string {
  return `Здравствуйте, ${s.client}! Спасибо за рекомендацию — отправляем вам сары ${money(s.amount)}. С уважением, MANSBAND.`;
}

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

type StatusFilter = "all" | SaryPayout["status"] | "not_found";

const STATUS_TABS: Array<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "Все" },
  { id: "pending", label: "К отправке" },
  { id: "not_found", label: "Не найдено" },
  { id: "in_check", label: "Учтено в чеке" },
  { id: "sent", label: "Отправлено" },
];

function matchesPhone(s: SaryPayout, q: string): boolean {
  const digits = q.replace(/\D/g, "");
  if (!digits) return true;
  return s.phone.replace(/\D/g, "").includes(digits);
}

/**
 * Телефон друга не нашёлся в базе при продаже (созвон 04.09): САР не блокируем,
 * но колл-менеджер обязан проверить номер по WhatsApp перед переводом.
 */
function NotFoundBadge({ s }: { s: SaryPayout }) {
  if (s.phoneFound) return null;
  return (
    <span className="chip bg-amber-400/10 text-amber-300/90 border border-amber-400/20 text-[11px] shrink-0">
      не найдено
    </span>
  );
}

/** Крупная кнопка в карточку заявки: доска открывает её по номеру из hash. */
function DealButton({ number }: { number?: number }) {
  if (!number) return null;
  return (
    <a
      href={`#board/all/all/${number}/sary`}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex items-center justify-center min-h-12 min-w-[9rem] px-6 rounded-xl bg-white text-ink-950 font-extrabold uppercase tracking-[0.16em] text-[15px] hover:bg-white/90 active:scale-[0.98] transition flex-1 sm:flex-none"
    >
      Заказ
    </a>
  );
}

/** `embedded` — внутри таба колл-менеджера на экране очередей: без своего заголовка. */
export function SaryScreen({ embedded = false }: { embedded?: boolean }) {
  const { sary, markSaryBatchSent, addSary, deals } = useStore();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({ client: "", phone: "", amount: "", reason: "", deal: "" });
  const [formError, setFormError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [phoneQuery, setPhoneQuery] = useState("");
  // «Не найдено» — не статус, а срез по всем трём разделам.
  const onlyNotFound = statusFilter === "not_found";
  const visible = useMemo(
    () => sary.filter((s) => matchesPhone(s, phoneQuery) && (!onlyNotFound || !s.phoneFound)),
    [sary, phoneQuery, onlyNotFound]
  );
  const pending = useMemo(() => visible.filter((s) => s.status === "pending"), [visible]);
  const inCheck = useMemo(
    () =>
      visible
        .filter((s) => s.status === "in_check")
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
    [visible]
  );
  const sent = useMemo(
    () =>
      visible
        .filter((s) => s.status === "sent")
        .sort((a, b) => (b.sentAt ?? b.createdAt).localeCompare(a.sentAt ?? a.createdAt)),
    [visible]
  );
  const notFoundCount = useMemo(() => sary.filter((s) => !s.phoneFound).length, [sary]);
  const pendingSum = pending.reduce((s, x) => s + x.amount, 0);
  const [copied, setCopied] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [photos, setPhotos] = useState<PhotoAttachment[]>([]);
  const [methodId, setMethodId] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const showPending = statusFilter === "all" || statusFilter === "pending" || onlyNotFound;
  const showInCheck = statusFilter === "all" || statusFilter === "in_check" || onlyNotFound;
  const showSent = statusFilter === "all" || statusFilter === "sent" || onlyNotFound;

  // Пачки по дню создания: кол-менеджер отправляет их одной серией переводов.
  const days = useMemo(() => {
    const map = new Map<string, SaryPayout[]>();
    for (const s of [...pending].sort((a, b) => b.createdAt.localeCompare(a.createdAt))) {
      const key = dayKey(s.createdAt);
      map.set(key, [...(map.get(key) ?? []), s]);
    }
    return [...map.entries()];
  }, [pending]);

  const selectedList = pending.filter((s) => selected.has(s.id));
  const selectedSum = selectedList.reduce((sum, s) => sum + s.amount, 0);
  const canSubmit = selectedList.length > 0 && photos.length > 0 && !!methodId && !submitting;

  function copyMsg(s: SaryPayout) {
    navigator.clipboard?.writeText(saryMessage(s)).catch(() => {});
    setCopied(s.id);
    setTimeout(() => setCopied((c) => (c === s.id ? null : c)), 1500);
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleDay(rows: SaryPayout[], allOn: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const row of rows) {
        if (allOn) next.delete(row.id);
        else next.add(row.id);
      }
      return next;
    });
  }

  function copyDay(rows: SaryPayout[]) {
    navigator.clipboard?.writeText(rows.map(saryMessage).join("\n\n")).catch(() => {});
    setCopied(rows[0]?.id ?? null);
    setTimeout(() => setCopied(null), 1500);
  }

  /** Подставляем «кто направил» из заявки: обычно сару платят именно ему. */
  function pickDeal(value: string) {
    const num = Number(value);
    const deal = deals.find((d) => d.number === num);
    setForm((f) => ({
      ...f,
      deal: value,
      client: f.client || deal?.referredBy || "",
      reason: f.reason || (deal ? `Рекомендация по заявке #${deal.number}` : ""),
    }));
  }

  function createSary() {
    const amount = Number(form.amount);
    if (form.client.trim().length < 2) return setFormError("Укажите, кому платим");
    if (form.phone.trim().length < 5) return setFormError("Нужен телефон получателя");
    if (!amount || amount <= 0) return setFormError("Сумма больше нуля");
    if (form.reason.trim().length < 2) return setFormError("Укажите основание");
    const refDealNumber = Number(form.deal);
    addSary({
      client: form.client.trim(),
      phone: form.phone.trim(),
      amount,
      reason: form.reason.trim(),
      refDealNumber: Number.isFinite(refDealNumber) && refDealNumber > 0 ? refDealNumber : undefined,
    });
    setForm({ client: "", phone: "", amount: "", reason: "", deal: "" });
    setFormError(null);
    setFormOpen(false);
  }

  async function submit() {
    if (selectedList.length === 0) return;
    if (photos.length === 0) {
      setSubmitError("Приложите скриншот перевода — без него отметить нельзя");
      return;
    }
    if (!methodId) {
      setSubmitError("Выберите счёт, с которого переведена пачка");
      return;
    }
    const [shot] = attachmentsToUpload(photos);
    if (!shot?.contentBase64) {
      setSubmitError("Не удалось прочитать скриншот — загрузите ещё раз");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await markSaryBatchSent(
        selectedList.map((s) => s.id),
        { filename: shot.filename, contentBase64: shot.contentBase64 },
        methodId
      );
      setSelected(new Set());
      setPhotos([]);
      setMethodId("");
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Не удалось отметить отправленными";
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={embedded ? "pb-40" : "max-w-5xl mx-auto pb-40"}>
      {!embedded && (
        <div className="flex items-center gap-2 mb-1">
          <Gift className="text-gold" size={22} />
          <h1 className="text-2xl font-extrabold text-white">Ведомость САР</h1>
        </div>
      )}
      <Hint>
        <p>
          Реферальная выплата за рекомендацию. Сколько костюмов в чеке — столько записей САР. Если
          костюмов нет — одна запись, и только при чеке от порога в «Настройки мотивации» (по
          умолчанию 20 000 ₽). Сумма по умолчанию 1 000 ₽.
        </p>
        <p>
          <b>К отправке:</b> отметьте пачку, переведите, выберите счёт и приложите скрин — без
          скрина статус «отправлено» не ставится. Отправленная пачка пишется расходом «Программа
          лояльности».
        </p>
        <p>
          <b>Учтено в чеке</b> — бонус уже скидкой при продаже, перевод не нужен. <b>Не найдено</b> —
          телефон друга не нашёлся в базе; САР не блокируется, колл-менеджер проверяет номер в
          WhatsApp до перевода.
        </p>
      </Hint>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="К отправке" value={String(pending.length)} tone="amber" sub={money(pendingSum)} />
        <StatTile label="Не найдено" value={String(notFoundCount)} tone="amber" sub="проверить номер" />
        <StatTile label="Учтено в чеке" value={String(inCheck.length)} tone="blue" />
        <StatTile label="Отправлено" value={String(sent.length)} tone="green" />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-5">
        {STATUS_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setStatusFilter(t.id)}
            className={`chip transition ${
              statusFilter === t.id ? "bg-gold text-ink-950 font-semibold" : "bg-ink-700 text-mute-soft hover:text-white"
            }`}
          >
            {t.label}
          </button>
        ))}
        <div className="relative flex-1 min-w-[180px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-mute" />
          <input
            className="input pl-8 py-1.5 text-[13px]"
            inputMode="tel"
            placeholder="Поиск по телефону"
            value={phoneQuery}
            onChange={(e) => setPhoneQuery(e.target.value)}
          />
        </div>
        <button className="chip bg-ink-700 text-mute-soft hover:text-white inline-flex items-center gap-1.5" onClick={() => setFormOpen(true)}>
          <Plus size={13} /> Добавить
        </button>
      </div>

      <Modal
        open={formOpen}
        onClose={() => {
          setFormOpen(false);
          setFormError(null);
        }}
        title="Добавить сару"
      >
        <div className="space-y-4">
          <div className="grid sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="field-label">Заявка (необязательно)</span>
              <input
                className="input"
                inputMode="numeric"
                placeholder="Номер заявки"
                value={form.deal}
                onChange={(e) => pickDeal(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="field-label">Кому платим</span>
              <input
                className="input"
                placeholder="Имя получателя"
                value={form.client}
                onChange={(e) => setForm((f) => ({ ...f, client: e.target.value }))}
              />
            </label>
            <label className="block">
              <span className="field-label">Телефон</span>
              <input
                className="input"
                inputMode="tel"
                placeholder="+7…"
                value={form.phone}
                onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
              />
            </label>
            <label className="block">
              <span className="field-label">Сумма</span>
              <input
                className="input"
                inputMode="numeric"
                placeholder="0"
                value={form.amount}
                onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
              />
            </label>
          </div>
          <label className="block">
            <span className="field-label">Основание</span>
            <input
              className="input"
              placeholder="За что платим"
              value={form.reason}
              onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            />
          </label>
          {formError && <div className="text-[13px] text-red-300">{formError}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="subtle" onClick={() => setFormOpen(false)}>
              Отмена
            </Button>
            <Button onClick={createSary}>
              <Plus size={16} /> В очередь
            </Button>
          </div>
        </div>
      </Modal>

      {showPending && (
        <>
          <div className="field-label mb-2">Не отправлено</div>
          <div className="space-y-4 mb-7">
            {days.length === 0 && (
              <div className="card text-center text-mute py-8">Все выплаты отправлены</div>
            )}
            {days.map(([day, rows]) => {
              const allOn = rows.every((row) => selected.has(row.id));
              const daySum = rows.reduce((sum, row) => sum + row.amount, 0);
              return (
                <section key={day} className="card p-0 overflow-hidden">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-ink-700">
                    <label className="inline-flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        className="w-4 h-4 accent-gold"
                        checked={allOn}
                        onChange={() => toggleDay(rows, allOn)}
                      />
                      <span className="text-white font-semibold">{shortDate(day)}</span>
                    </label>
                    <span className="chip bg-ink-700 text-mute">
                      {rows.length} · {money(daySum)}
                    </span>
                    <button
                      onClick={() => copyDay(rows)}
                      className="chip bg-ink-700 text-mute-soft hover:text-white inline-flex items-center gap-1.5 ml-auto"
                    >
                      <Copy size={13} /> Тексты за день
                    </button>
                  </div>
                  <div className="divide-y divide-ink-800">
                    {rows.map((s) => (
                      <div
                        key={s.id}
                        className="flex flex-col gap-3 px-4 py-4 hover:bg-ink-800/40 sm:flex-row sm:items-center"
                      >
                        <label className="flex items-start sm:items-center gap-3 flex-1 min-w-0 cursor-pointer">
                          <input
                            type="checkbox"
                            className="w-5 h-5 mt-1 sm:mt-0 accent-gold shrink-0"
                            checked={selected.has(s.id)}
                            onChange={() => toggle(s.id)}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-white font-semibold text-lg">{money(s.amount)}</span>
                              <span className="text-mute-soft text-[14px]">
                                {s.client} · {s.phone}
                              </span>
                              <NotFoundBadge s={s} />
                            </div>
                            <div className="text-mute text-[13px] mt-0.5">{s.reason}</div>
                          </div>
                        </label>
                        <div className="flex items-stretch gap-2 pl-8 sm:pl-0">
                          <button
                            type="button"
                            onClick={() => copyMsg(s)}
                            className="inline-flex items-center justify-center gap-1.5 min-h-12 px-4 rounded-xl border border-ink-700 bg-ink-900 text-mute-soft hover:text-white hover:border-white/30 shrink-0"
                          >
                            <Copy size={15} /> {copied === s.id ? "Скопировано" : "Текст"}
                          </button>
                          <DealButton number={s.refDealNumber} />
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}

      {selectedList.length > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-30 border-t border-ink-700 bg-ink-900/95 backdrop-blur px-4 py-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]">
          <div className="max-w-5xl mx-auto space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="text-white text-sm font-semibold">
                Выбрано {selectedList.length} · {money(selectedSum)}
              </div>
            </div>
            <div>
              <div className="field-label">С какого счёта переведено</div>
              <PaymentMethodSelect
                value={methodId}
                onChange={(v) => {
                  setMethodId(v);
                  setSubmitError(null);
                }}
                withCertificate={false}
                placeholder="— выбрать счёт —"
              />
            </div>
            <PhotoField
              photos={photos}
              onChange={(next) => {
                setPhotos(next.slice(0, 1));
                setSubmitError(null);
              }}
              label="Скрин перевода на пачку"
              required
            />
            {submitError && <div className="text-[13px] text-red-300">{submitError}</div>}
            <button
              onClick={() => void submit()}
              disabled={!canSubmit}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-4 py-2.5 hover:bg-white/90 active:scale-95 transition disabled:opacity-40 disabled:pointer-events-none"
            >
              <Check size={16} />{" "}
              {submitting
                ? "Сохраняем…"
                : photos.length === 0
                  ? "Сначала приложите скрин"
                  : !methodId
                    ? "Выберите счёт перевода"
                    : "Отметить отправленными"}
            </button>
          </div>
        </div>
      )}

      {showInCheck && (
        <>
          <div className="field-label mb-2 flex items-center gap-1.5">
            <BadgePercent size={13} /> Учтено в чеке
          </div>
          <div className="space-y-2 mb-7">
            {inCheck.length === 0 && (
              <div className="text-mute text-[13px] px-1 pb-2">Пока пусто</div>
            )}
            {inCheck.map((s) => (
              <div
                key={s.id}
                className="flex flex-col gap-3 px-4 py-4 rounded-xl bg-ink-900/50 border border-ink-800 sm:flex-row sm:items-center"
              >
                <div className="flex items-start sm:items-center gap-3 flex-1 min-w-0">
                  <BadgePercent size={18} className="text-sky-300/80 shrink-0 mt-0.5 sm:mt-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-white font-semibold text-[15px]">{s.client}</span>
                      <span className="text-mute-soft">{money(s.amount)} · скидка в чеке</span>
                      <NotFoundBadge s={s} />
                    </div>
                    <div className="text-mute text-[13px] mt-0.5">
                      {shortDate(s.createdAt)} {timeOf(s.createdAt)}
                    </div>
                  </div>
                </div>
                <DealButton number={s.refDealNumber} />
              </div>
            ))}
          </div>
        </>
      )}

      {showSent && (
        <>
          <div className="field-label mb-2 flex items-center gap-1.5">
            <Clock3 size={13} /> Отправлено
          </div>
          <div className="space-y-2">
            {sent.length === 0 && <div className="text-mute text-[13px] px-1">Пока пусто</div>}
            {sent.map((s) => (
              <div
                key={s.id}
                className="flex flex-col gap-3 px-4 py-4 rounded-xl bg-ink-900/50 border border-ink-800 sm:flex-row sm:items-center"
              >
                <div className="flex items-start sm:items-center gap-3 flex-1 min-w-0">
                  <Check size={18} className="text-white/70 shrink-0 mt-0.5 sm:mt-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-white font-semibold text-[15px]">{s.client}</span>
                      <span className="text-mute-soft">
                        {money(s.amount)}
                        {s.screenshotAttached ? " · скрин" : ""}
                      </span>
                      <NotFoundBadge s={s} />
                    </div>
                    <div className="text-mute text-[13px] mt-0.5" title="Дата отправки">
                      {s.sentAt
                        ? `${shortDate(s.sentAt)} ${timeOf(s.sentAt)}`
                        : `${shortDate(s.createdAt)} ${timeOf(s.createdAt)}`}
                    </div>
                  </div>
                </div>
                <DealButton number={s.refDealNumber} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
