import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Search, Filter, X, ChevronDown, ChevronUp } from "lucide-react";
import { useStore } from "../store";
import { money, shortDate, timeOf } from "../lib/format";
import { Badge, Button, StageBadge } from "../components/ui";
import { FUNNEL_LABEL, KIND_LABEL } from "../lib/labels";
import {
  CONSULTANTS,
  HIDDEN_STAGES,
  SALE_STAGES,
  STORES,
} from "../data/mock";
import type { Deal, DealKind } from "../data/types";
import { DealWorkspace } from "./DealWorkspace";

/**
 * Фильтры на доске.
 * «Отложка» = kind=deferred ИЛИ продажа (sale) на товарных этапах.
 * Компания / аренда / обещание сюда по этапу не попадают — после смены типа
 * заявка уходит в свой раздел (правки владельца).
 */
const KIND_GROUPS: {
  id: string;
  label: string;
  kinds: DealKind[] | null;
  /** Только для вкладки «Отложка»: доп. этапы у kind=sale. */
  saleStages?: string[];
}[] = [
  { id: "all", label: "Все", kinds: null },
  { id: "sale", label: "Продажи", kinds: ["sale"] },
  {
    id: "deferred",
    label: "Отложка",
    kinds: ["deferred"],
    saleStages: ["Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен"],
  },
  { id: "promise", label: "Обещания", kinds: ["promise"] },
  { id: "rental", label: "Аренда", kinds: ["rental"] },
  { id: "cert", label: "Сертификаты", kinds: ["cert_plastic", "cert_digital"] },
  { id: "company", label: "Продажи компании", kinds: ["company"] },
  { id: "delivery", label: "Доставки", kinds: ["delivery"] },
  { id: "defect_all", label: "Все дефекты", kinds: ["defect", "drycleaning", "resew", "wrong_size", "wrong_label"] },
  { id: "defect", label: "Браки", kinds: ["defect"] },
  { id: "drycleaning", label: "Химчистка", kinds: ["drycleaning"] },
  { id: "resew", label: "Перешив", kinds: ["resew"] },
  { id: "wrong_size", label: "Перепутан размер", kinds: ["wrong_size"] },
  { id: "wrong_label", label: "Некорректная бирка", kinds: ["wrong_label"] },
];

function matchesKindGroup(
  d: Deal,
  group: (typeof KIND_GROUPS)[number]
): boolean {
  if (!group.kinds) return true;
  if (group.kinds.includes(d.kind)) return true;
  // Продажи на этапах отложки — во вкладке «Отложка», но не компания/аренда.
  if (group.saleStages && d.kind === "sale" && group.saleStages.includes(d.stage)) {
    return true;
  }
  return false;
}

const STATUS_FILTERS = [
  { id: "all", label: "Все" },
  { id: "open", label: "Открытые" },
  { id: "success", label: "Успешные" },
  { id: "fail", label: "Провальные" },
  { id: "overdue", label: "Просроченные" },
] as const;

const STORE_OPTIONS = STORES.filter((s) => s !== "Онлайн-магазин");
const CONSULTANT_OPTIONS = CONSULTANTS.filter((c) => c.role === "consultant").map((c) => c.name);

/** Контрольная дата заявки (срок отложки / актуальности / аренды). */
function dealDeadline(d: Deal): string | undefined {
  return d.reservedUntil || d.actualUntil || d.deferredUntil || d.validUntil || d.rentalTo;
}

function isSuccess(d: Deal): boolean {
  return d.stage === "Успех";
}

function isFail(d: Deal): boolean {
  return d.stage === "Провал";
}

function isClosed(d: Deal): boolean {
  return isSuccess(d) || isFail(d);
}

function isOverdue(d: Deal, today: string): boolean {
  if (isClosed(d)) return false;
  const dl = dealDeadline(d);
  return !!dl && dl < today;
}

function createdDay(iso: string): string {
  return iso.slice(0, 10);
}

export function DealsBoard({
  onNew,
  onReturnExchange,
  initialGroup,
  initialStatus,
  initialDealNumber,
  onBoardChange,
  origin,
}: {
  onNew: () => void;
  onReturnExchange?: (kind: "refund" | "exchange", dealNumber: number) => void;
  initialGroup?: string | null;
  initialStatus?: string | null;
  initialDealNumber?: number | null;
  onBoardChange?: (group: string, status: string) => void;
  /** Откуда открыли карточку (очередь Эдвина, Миши, экран задач) — туда и вернёмся. */
  origin?: string | null;
}) {
  const { deals, dealsLoading } = useStore();
  const today = new Date().toISOString().slice(0, 10);
  const [q, setQ] = useState("");
  const [kindGroup, setKindGroup] = useState<string>(initialGroup ?? "all");
  const [statusFilter, setStatusFilter] = useState<string>(initialStatus ?? "all");
  const [storeFilter, setStoreFilter] = useState<string>("all");
  const [consultantFilter, setConsultantFilter] = useState<string>("all");
  const [stageFilter, setStageFilter] = useState<string>("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [active, setActive] = useState<Deal | null>(null);
  const [visibleCount, setVisibleCount] = useState(60);
  const [filtersOpen, setFiltersOpen] = useState(false);

  useEffect(() => {
    if (!initialDealNumber) return;
    const deal = deals.find((row) => row.number === initialDealNumber);
    if (deal) setActive(deal);
  }, [initialDealNumber, deals]);

  // Пресет из URL (#board/<group>/<status>). Старые ссылки на дефекты → «Все».
  useEffect(() => {
    const next = initialGroup ?? "all";
    setKindGroup(KIND_GROUPS.some((g) => g.id === next) ? next : "all");
  }, [initialGroup]);
  useEffect(() => {
    // Старые ссылки #board/.../done → успешные
    const s = initialStatus === "done" ? "success" : (initialStatus ?? "all");
    setStatusFilter(s);
  }, [initialStatus]);

  /**
   * Закрытие карточки: вернуться туда, откуда зашли (очередь Эдвина, Миши,
   * очереди задач). Если пришли с доски — убрать номер заявки из хэша, сохранив
   * фильтры среза.
   */
  function closeDeal() {
    setActive(null);
    if (origin) {
      // origin приходит одним сегментом: «tasks:crm:pending» → «tasks/crm/pending».
      window.location.hash = origin.replace(/:/g, "/");
      return;
    }
    const next = `board/${kindGroup}/${statusFilter}`;
    if (window.location.hash.replace(/^#/, "") !== next) window.location.hash = next;
  }

  function pickGroup(id: string) {
    setKindGroup(id);
    onBoardChange?.(id, statusFilter);
  }

  function pickStatus(id: string) {
    setStatusFilter(id);
    onBoardChange?.(kindGroup, id);
  }

  function resetExtraFilters() {
    setStoreFilter("all");
    setConsultantFilter("all");
    setStageFilter("all");
    setDateFrom("");
    setDateTo("");
  }

  const group = KIND_GROUPS.find((g) => g.id === kindGroup) ?? KIND_GROUPS[0];

  const stageOptions = useMemo(() => {
    const set = new Set<string>(SALE_STAGES);
    for (const d of deals) {
      if (!HIDDEN_STAGES.has(d.stage)) set.add(d.stage);
    }
    return Array.from(set).filter((s) => !HIDDEN_STAGES.has(s));
  }, [deals]);

  const extraActive =
    storeFilter !== "all" ||
    consultantFilter !== "all" ||
    stageFilter !== "all" ||
    !!dateFrom ||
    !!dateTo;

  const filtered = useMemo(() => {
    const list = deals.filter((d) => {
      const matchQ =
        d.clientName.toLowerCase().includes(q.toLowerCase()) ||
        d.clientPhone.includes(q) ||
        String(d.number).includes(q);
      const matchKind = matchesKindGroup(d, group);
      const matchStatus =
        statusFilter === "all"
          ? true
          : statusFilter === "open"
            ? !isClosed(d)
            : statusFilter === "success"
              ? isSuccess(d)
              : statusFilter === "fail"
                ? isFail(d)
                : isOverdue(d, today);
      const matchStore = storeFilter === "all" || d.store === storeFilter;
      const matchConsultant = consultantFilter === "all" || d.consultant === consultantFilter;
      const matchStage = stageFilter === "all" || d.stage === stageFilter;
      const day = createdDay(d.createdAt);
      const matchFrom = !dateFrom || day >= dateFrom;
      const matchTo = !dateTo || day <= dateTo;
      return (
        matchQ &&
        matchKind &&
        matchStatus &&
        matchStore &&
        matchConsultant &&
        matchStage &&
        matchFrom &&
        matchTo
      );
    });
    // Новее сверху
    return list.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  }, [
    deals,
    q,
    group,
    statusFilter,
    storeFilter,
    consultantFilter,
    stageFilter,
    dateFrom,
    dateTo,
    today,
  ]);

  // Сброс пагинации при смене фильтров — иначе «пусто» при узком срезе.
  useEffect(() => {
    setVisibleCount(60);
  }, [q, kindGroup, statusFilter, storeFilter, consultantFilter, stageFilter, dateFrom, dateTo]);

  const visible = filtered.slice(0, visibleCount);
  const hasMore = filtered.length > visibleCount;

  if (active) {
    return (
      <DealWorkspace
        deal={active}
        onClose={closeDeal}
        onReturnExchange={onReturnExchange}
      />
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Заявки</h1>
          <p className="text-mute text-sm mt-0.5">
            {dealsLoading
              ? "Загрузка заявок…"
              : `${filtered.length} из ${deals.length} · все операции магазина`}
          </p>
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

      <div className="flex gap-2 mb-3 flex-wrap">
        <div className="relative flex-1 min-w-0 w-full sm:min-w-[220px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input
            className="input pl-9"
            placeholder="Поиск по имени, телефону, № заявки…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="inline-flex flex-wrap rounded-lg border border-ink-700 max-w-full overflow-hidden">
          {STATUS_FILTERS.map((f) => (
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

      {/* Фильтры сворачиваются — на телефоне не занимают полэкрана до таблицы */}
      <div className="card mb-4 p-0 overflow-hidden">
        <button
          type="button"
          onClick={() => setFiltersOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-ink-800/40"
        >
          <Filter size={15} className="text-mute" />
          <span className="text-[13px] font-medium text-white flex-1">
            Фильтры{extraActive ? " · активны" : ""}
          </span>
          {extraActive && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                resetExtraFilters();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.stopPropagation();
                  resetExtraFilters();
                }
              }}
              className="inline-flex items-center gap-1 text-[12px] text-mute hover:text-white px-2"
            >
              <X size={13} /> Сбросить
            </span>
          )}
          {filtersOpen ? <ChevronUp size={16} className="text-mute" /> : <ChevronDown size={16} className="text-mute" />}
        </button>
        {filtersOpen && (
          <div className="px-3 pb-3 flex flex-wrap gap-2 items-end border-t border-ink-800 pt-3">
            <div className="min-w-[140px] flex-1">
              <div className="field-label">Магазин</div>
              <select className="input py-2 text-sm" value={storeFilter} onChange={(e) => setStoreFilter(e.target.value)}>
                <option value="all">Все магазины</option>
                {STORE_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[140px] flex-1">
              <div className="field-label">Консультант</div>
              <select
                className="input py-2 text-sm"
                value={consultantFilter}
                onChange={(e) => setConsultantFilter(e.target.value)}
              >
                <option value="all">Все</option>
                {CONSULTANT_OPTIONS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[140px] flex-1">
              <div className="field-label">Этап</div>
              <select className="input py-2 text-sm" value={stageFilter} onChange={(e) => setStageFilter(e.target.value)}>
                <option value="all">Все этапы</option>
                {stageOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[120px]">
              <div className="field-label">Создана с</div>
              <input type="date" className="input py-2 text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </div>
            <div className="min-w-[120px]">
              <div className="field-label">по</div>
              <input type="date" className="input py-2 text-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </div>
          </div>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <div
          className="overflow-x-auto overscroll-x-contain deals-table-scroll"
          style={{ WebkitOverflowScrolling: "touch", touchAction: "pan-x pan-y" }}
        >
          <table className="w-full table-fixed text-left border-collapse text-[13px]">
            <colgroup>
              <col style={{ width: "7.25rem" }} />
              <col className="w-[8%]" />
              <col />
              <col className="w-[8%]" />
              <col className="w-[10%]" />
              <col className="w-[11%]" />
              <col className="w-[10%]" />
              <col className="w-[9%]" />
              <col className="w-[14%]" />
              <col style={{ width: "1.75rem" }} />
            </colgroup>
            <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-700">
              <tr>
                <th className="px-2 py-2.5 whitespace-nowrap">№</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Дата</th>
                <th className="px-2 py-2.5">Клиент</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Тип</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Вид</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Магазин</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Консультант</th>
                <th className="px-2 py-2.5 whitespace-nowrap text-right">К оплате</th>
                <th className="px-2 py-2.5 whitespace-nowrap">Этап</th>
                <th className="px-1 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {visible.map((d) => (
                <tr
                  key={d.id}
                  className="hover:bg-ink-800/40 cursor-pointer transition"
                  onClick={() => setActive(d)}
                >
                  <td className="px-2 py-2.5 font-mono text-mute whitespace-nowrap align-top">
                    #{d.number}
                  </td>
                  <td className="px-2 py-2.5 align-top text-mute whitespace-nowrap">
                    <div>{shortDate(d.createdAt)}</div>
                    <div className="text-[11px] text-mute/80">{timeOf(d.createdAt)}</div>
                  </td>
                  <td className="px-2 py-2.5 align-top min-w-0">
                    <div className="text-white font-medium truncate leading-snug" title={d.clientName}>
                      {d.clientName}
                    </div>
                    <div className="text-[11px] text-mute truncate mt-0.5">{d.clientPhone || "—"}</div>
                  </td>
                  <td className="px-2 py-2.5 align-top min-w-0">
                    <Badge tone="gray">{FUNNEL_LABEL[d.funnel]}</Badge>
                  </td>
                  <td className="px-2 py-2.5 align-top min-w-0">
                    <Badge tone="blue">{KIND_LABEL[d.kind]}</Badge>
                  </td>
                  <td
                    className="px-2 py-2.5 align-top text-mute-soft truncate"
                    title={d.store}
                  >
                    {d.store.replace(/^На\s+/i, "")}
                  </td>
                  <td className="px-2 py-2.5 align-top text-mute-soft truncate" title={d.consultant || undefined}>
                    {d.consultant || "—"}
                  </td>
                  <td className="px-2 py-2.5 align-top font-semibold text-white whitespace-nowrap text-right tabular-nums">
                    {d.total ? money(d.total) : "—"}
                  </td>
                  <td className="px-2 py-2.5 align-top min-w-0">
                    <StageBadge stage={d.stage} className="inline-block align-top" />
                  </td>
                  <td className="px-1 py-2.5 align-top text-mute">
                    <ArrowRight size={14} />
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-10 text-center text-mute">
                    <Filter size={20} className="mx-auto mb-2 opacity-50" />
                    {dealsLoading ? "Загрузка заявок…" : "Ничего не найдено"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {hasMore && (
          <div className="p-3 border-t border-ink-700 text-center">
            <Button variant="subtle" onClick={() => setVisibleCount((n) => n + 60)}>
              Показать ещё ({filtered.length - visibleCount})
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
