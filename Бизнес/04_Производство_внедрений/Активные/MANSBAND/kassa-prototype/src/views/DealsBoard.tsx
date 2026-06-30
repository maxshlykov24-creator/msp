import { useEffect, useState } from "react";
import { Search, Filter, ArrowRight } from "lucide-react";
import { useStore } from "../store";
import { money, shortDate, timeOf, dateCompact, dateRangeCompact } from "../lib/format";
import { Badge, Button, Modal, StageBadge } from "../components/ui";
import { FUNNEL_LABEL, KIND_LABEL } from "../lib/labels";
import { PAYMENT_METHODS, STAGES_BY_KIND, STORE_ADDRESS } from "../data/mock";
import type { Deal, DealKind } from "../data/types";

// Подразделы заявок по виду (созвон 14.06) + разнесённые виды дефектов (правки 7)
const KIND_GROUPS: { id: string; label: string; kinds: DealKind[] | null }[] = [
  { id: "all", label: "Все", kinds: null },
  { id: "sale", label: "Продажи", kinds: ["sale"] },
  { id: "rental", label: "Аренда", kinds: ["rental"] },
  { id: "cert", label: "Сертификаты", kinds: ["cert_plastic", "cert_digital"] },
  { id: "company", label: "Продажи компании", kinds: ["company"] },
  { id: "delivery", label: "Доставки", kinds: ["delivery"] },
  { id: "defect_all", label: "Все дефекты", kinds: ["defect", "drycleaning", "resew", "wrong_size"] },
  { id: "defect", label: "Браки", kinds: ["defect"] },
  { id: "drycleaning", label: "Химчистка", kinds: ["drycleaning"] },
  { id: "resew", label: "Перешив", kinds: ["resew"] },
  { id: "wrong_size", label: "Перепутан размер", kinds: ["wrong_size"] },
];

/** Контрольная дата заявки (срок отложки / актуальности / аренды). */
function dealDeadline(d: Deal): string | undefined {
  return d.reservedUntil || d.actualUntil || d.deferredUntil || d.validUntil || d.rentalTo;
}

function isClosed(d: Deal): boolean {
  return ["Успех", "Провал"].includes(d.stage);
}

function isOverdue(d: Deal, today: string): boolean {
  if (isClosed(d)) return false;
  const dl = dealDeadline(d);
  return !!dl && dl < today;
}

export function DealsBoard({
  onNew,
  initialGroup,
  initialStatus,
  onBoardChange,
}: {
  onNew: () => void;
  initialGroup?: string | null;
  initialStatus?: string | null;
  onBoardChange?: (group: string, status: string) => void;
}) {
  const { deals } = useStore();
  const today = new Date().toISOString().slice(0, 10);
  const [q, setQ] = useState("");
  const [kindGroup, setKindGroup] = useState<string>(initialGroup ?? "all");
  const [statusFilter, setStatusFilter] = useState<string>(initialStatus ?? "all");
  const [active, setActive] = useState<Deal | null>(null);

  // Пресет из левого меню (#board/<group>/<status>) — синхронизация при навигации.
  useEffect(() => {
    setKindGroup(initialGroup ?? "all");
  }, [initialGroup]);
  useEffect(() => {
    setStatusFilter(initialStatus ?? "all");
  }, [initialStatus]);

  function pickGroup(id: string) {
    setKindGroup(id);
    onBoardChange?.(id, statusFilter);
  }

  function pickStatus(id: string) {
    setStatusFilter(id);
    onBoardChange?.(kindGroup, id);
  }

  const group = KIND_GROUPS.find((g) => g.id === kindGroup) ?? KIND_GROUPS[0];

  const filtered = deals.filter((d) => {
    const matchQ =
      d.clientName.toLowerCase().includes(q.toLowerCase()) ||
      d.clientPhone.includes(q) ||
      String(d.number).includes(q);
    const matchKind = !group.kinds || group.kinds.includes(d.kind);
    const matchStatus =
      statusFilter === "all"
        ? true
        : statusFilter === "open"
        ? !isClosed(d)
        : statusFilter === "done"
        ? isClosed(d)
        : isOverdue(d, today);
    return matchQ && matchKind && matchStatus;
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Заявки</h1>
          <p className="text-mute text-sm mt-0.5">Все операции магазина в одном месте</p>
        </div>
        <Button onClick={onNew}>+ Новая заявка</Button>
      </div>

      {/* Подразделы по виду заявки */}
      <div className="flex gap-1.5 mb-3 flex-wrap">
        {KIND_GROUPS.map((g) => (
          <button
            key={g.id}
            onClick={() => pickGroup(g.id)}
            className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
              kindGroup === g.id ? "bg-gold/15 text-gold-soft border border-gold/40" : "text-mute hover:bg-ink-800 border border-transparent"
            }`}
          >
            {g.label}
          </button>
        ))}
      </div>

      <div className="flex gap-2 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input
            className="input pl-9"
            placeholder="Поиск по имени, телефону, № заявки…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="inline-flex rounded-lg border border-ink-700 overflow-hidden">
          {[
            { id: "all", label: "Все" },
            { id: "open", label: "Открытые" },
            { id: "done", label: "Завершённые" },
            { id: "overdue", label: "Просроченные" },
          ].map((f) => (
            <button
              key={f.id}
              onClick={() => pickStatus(f.id)}
              className={`px-3 py-2 text-[13px] font-medium ${
                statusFilter === f.id ? "bg-gold text-ink-950" : "text-mute hover:bg-ink-800"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-left">
          <thead className="text-[12px] uppercase tracking-wider text-mute border-b border-ink-700">
            <tr>
              <th className="px-4 py-3">№</th>
              <th className="px-4 py-3">Клиент</th>
              <th className="px-4 py-3 hidden lg:table-cell">Тип</th>
              <th className="px-4 py-3 hidden lg:table-cell">Вид</th>
              <th className="px-4 py-3 hidden md:table-cell">Консультант</th>
              <th className="px-4 py-3">Сумма</th>
              <th className="px-4 py-3">Этап</th>
              <th className="px-4 py-3 hidden xl:table-cell">Создана</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {filtered.map((d) => (
              <tr
                key={d.id}
                className="hover:bg-ink-800/40 cursor-pointer transition"
                onClick={() => setActive(d)}
              >
                <td className="px-4 py-3 font-mono text-mute">#{d.number}</td>
                <td className="px-4 py-3">
                  <div className="text-white font-medium">{d.clientName}</div>
                  <div className="text-[12px] text-mute">{d.clientPhone}</div>
                  <div className="flex gap-1 mt-1 lg:hidden">
                    <Badge tone="gray">{FUNNEL_LABEL[d.funnel]}</Badge>
                    <Badge tone="blue">{KIND_LABEL[d.kind]}</Badge>
                  </div>
                </td>
                <td className="px-4 py-3 hidden lg:table-cell">
                  <Badge tone="gray">{FUNNEL_LABEL[d.funnel]}</Badge>
                </td>
                <td className="px-4 py-3 hidden lg:table-cell">
                  <Badge tone="blue">{KIND_LABEL[d.kind]}</Badge>
                </td>
                <td className="px-4 py-3 hidden md:table-cell text-mute-soft text-sm">{d.consultant}</td>
                <td className="px-4 py-3 font-semibold text-white">{d.total ? money(d.total) : "—"}</td>
                <td className="px-4 py-3"><StageBadge stage={d.stage} /></td>
                <td className="px-4 py-3 hidden xl:table-cell text-mute text-[13px]">
                  {shortDate(d.createdAt)} {timeOf(d.createdAt)}
                </td>
                <td className="px-4 py-3 text-mute"><ArrowRight size={16} /></td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-mute">
                  <Filter size={20} className="mx-auto mb-2 opacity-50" />
                  Ничего не найдено
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <DealModal deal={active} onClose={() => setActive(null)} />
    </div>
  );
}

function DealModal({ deal, onClose }: { deal: Deal | null; onClose: () => void }) {
  const { deals, updateDealStage, addDealComment, activeConsultant } = useStore();
  const [stage, setStage] = useState(deal?.stage ?? "");
  const [stageSaved, setStageSaved] = useState(false);
  const [comment, setComment] = useState("");

  useEffect(() => {
    setStage(deal?.stage ?? "");
    setStageSaved(false);
    setComment("");
  }, [deal]);

  if (!deal) return null;
  // Живая версия заявки из стора — чтобы история и оплата обновлялись после действий.
  const live = deals.find((d) => d.id === deal.id) ?? deal;
  const paid = live.payments.reduce((s, p) => s + p.amount, 0);
  const remainder = live.total - paid;
  const stages = STAGES_BY_KIND[deal.kind] ?? [];
  const stageOptions = stages.includes(stage) ? stages : [stage, ...stages];
  // Запрет перехода в «Успех» при незакрытом остатке (правки 7).
  const successBlocked = stage === "Успех" && remainder > 0;
  const history = live.history ?? [];

  function saveStage() {
    if (!deal || stage === live.stage || successBlocked) return;
    updateDealStage(deal.id, stage);
    setStageSaved(true);
  }

  function postComment() {
    if (!deal || !comment.trim()) return;
    addDealComment(deal.id, comment.trim(), activeConsultant);
    setComment("");
  }

  return (
    <Modal open={!!deal} onClose={onClose} title={`Заявка #${deal.number}`} wide>
      <div className="grid md:grid-cols-2 gap-5">
        <div className="space-y-2 text-sm">
          <Row k="Клиент" v={deal.clientName} />
          <Row k="Телефон" v={deal.clientPhone} />
          <Row k="Тип" v={FUNNEL_LABEL[deal.funnel]} />
          <Row k="Вид" v={KIND_LABEL[deal.kind]} />
          <Row k="Консультант" v={deal.consultant} />
          {deal.referredBy && <Row k="Направил (слив)" v={deal.referredBy} />}
          {deal.callManager && <Row k="Call-менеджер" v={deal.callManager} />}
          <Row k="Магазин" v={deal.store} />
          <Row k="Адрес" v={deal.storeAddress ?? STORE_ADDRESS[deal.store]} />
          {deal.recipientName && <Row k="Получатель" v={`${deal.recipientName} · ${deal.recipientPhone ?? ""}`} />}
          {deal.deliveryCity && <Row k="Город доставки" v={deal.deliveryCity} />}
          {deal.linkedDealNumber && <Row k="Исходная заявка" v={`#${deal.linkedDealNumber}`} />}
          {deal.companyName && <Row k="Компания" v={deal.companyName} />}
          {deal.invoiceNo && <Row k="№ счёта" v={deal.invoiceNo} />}
          {deal.channel && <Row k="Канал" v={deal.channel} />}
          {deal.purpose && <Row k="Цель" v={deal.purpose} />}
          {deal.certificateNumber && <Row k="№ сертификата" v={deal.certificateNumber} />}
          {deal.rentalFrom && <Row k="Аренда" v={dateRangeCompact(deal.rentalFrom, deal.rentalTo)} />}
          {deal.reservedUntil && <Row k="Резерв до" v={dateCompact(deal.reservedUntil)} />}
          {deal.actualUntil && <Row k="Актуально до" v={dateCompact(deal.actualUntil)} />}
          {deal.deferredUntil && <Row k="Отложка до" v={dateCompact(deal.deferredUntil)} />}
          {deal.validUntil && <Row k="Действует до" v={dateCompact(deal.validUntil)} />}
          {deal.receivedAt && <Row k="Получен" v={dateCompact(deal.receivedAt)} />}
          {deal.issueDate && <Row k="Выдача (факт)" v={dateCompact(deal.issueDate)} />}
          {deal.returnDate && <Row k="Возврат (факт)" v={dateCompact(deal.returnDate)} />}
          {deal.paymentDate && <Row k="Дата оплаты" v={dateCompact(deal.paymentDate)} />}
          {!!deal.tips && <Row k="Чаевые (Эдвин)" v={money(deal.tips)} />}
          {deal.comment && <Row k="Комментарий" v={deal.comment} />}
          <div className="pt-2">
            <div className="field-label">Этап заявки</div>
            <div className="flex items-center gap-2 flex-wrap">
              <StageBadge stage={stage} />
              <select
                className="input w-auto text-sm"
                value={stage}
                onChange={(e) => { setStage(e.target.value); setStageSaved(false); }}
              >
                {stageOptions.map((s) => <option key={s}>{s}</option>)}
              </select>
              <Button
                variant="subtle"
                disabled={stage === live.stage || stageSaved || successBlocked}
                onClick={saveStage}
              >
                {stageSaved ? "Сохранено" : "Сменить этап"}
              </Button>
            </div>
            {successBlocked && (
              <div className="mt-1.5 text-[12px] text-amber-300/90">
                Остаток {money(remainder)} — «Успех» недоступен, пока заявка не оплачена полностью.
              </div>
            )}
          </div>
        </div>
        <div>
          <div className="field-label">Состав</div>
          <div className="rounded-lg border border-ink-700 divide-y divide-ink-800 mb-3">
            {deal.items.map((it, i) => (
              <div key={i} className="flex justify-between px-3 py-2 text-sm">
                <span className="text-mute-soft">{it.name} × {it.qty}</span>
                <span className="text-white">{it.noPrice ? "—" : money(it.price * it.qty)}</span>
              </div>
            ))}
          </div>
          {deal.payments.length > 0 && (
            <>
              <div className="field-label">Оплаты</div>
              <div className="rounded-lg border border-ink-700 divide-y divide-ink-800">
                {deal.payments.map((p) => {
                  const m = PAYMENT_METHODS.find((x) => x.id === p.methodId);
                  return (
                    <div key={p.id} className="flex justify-between px-3 py-2 text-sm">
                      <span className="text-mute-soft">{m?.label ?? p.methodId}</span>
                      <span className="text-white">{money(p.amount)}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          <div className="flex justify-between mt-3 text-sm">
            <span className="text-mute">Итого / оплачено</span>
            <span className="text-white font-semibold">
              {money(live.total)} / {money(paid)}
            </span>
          </div>
        </div>
      </div>

      {/* История действий по заявке */}
      <div className="mt-5 pt-5 border-t border-ink-800">
        <div className="field-label mb-2">История</div>
        {history.length > 0 ? (
          <ol className="space-y-2.5 mb-4">
            {[...history].reverse().map((h, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-gold shrink-0" />
                <div className="min-w-0">
                  <div className="text-white">{h.action}</div>
                  <div className="text-[12px] text-mute">
                    {shortDate(h.at)} {timeOf(h.at)} · {h.who}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <div className="text-sm text-mute mb-4">Действий пока нет.</div>
        )}
        <div className="flex gap-2">
          <input
            className="input text-sm"
            placeholder="Добавить комментарий…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && postComment()}
          />
          <Button variant="subtle" disabled={!comment.trim()} onClick={postComment}>
            Добавить
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-ink-800/60 pb-1.5">
      <span className="text-mute">{k}</span>
      <span className="text-white text-right">{v}</span>
    </div>
  );
}
