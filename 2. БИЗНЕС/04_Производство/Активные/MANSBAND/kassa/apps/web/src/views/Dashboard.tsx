import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, ChevronRight, Store, Loader2, ShoppingCart } from "lucide-react";
import { StatTile, Card } from "../components/ui";
import { moneyPlain } from "../lib/format";
import { api, USE_MOCK } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Hint } from "../lib/hints";

// Формат сводки — формулы Миши (созвон 20.08), см. apps/api/src/services/stats.ts.

interface StatsRow {
  name: string;
  clients: number;
  success: number;
  revenue: number;
  avgCheck: number;
  conversion: number | null;
  upt: number | null;
  slivs: number;
  noSlivs: number;
  rescuedSlivs: number;
}

interface PurchasingStats {
  revenue: number;
  expensesTotal: number;
  expensesByCategory: Record<string, number>;
  positionsByGroup: Array<{ group: string; qty: number; revenue: number }>;
  byChannel: Record<string, number>;
  byPurpose: Record<string, number>;
}

interface StatsSummary {
  totals: StatsRow;
  consultants: StatsRow[];
  stores: StatsRow[];
  purchasing: PurchasingStats;
}

const EMPTY_ROW: StatsRow = {
  name: "Все",
  clients: 0,
  success: 0,
  revenue: 0,
  avgCheck: 0,
  conversion: null,
  upt: null,
  slivs: 0,
  noSlivs: 0,
  rescuedSlivs: 0,
};

const EMPTY_SUMMARY: StatsSummary = {
  totals: EMPTY_ROW,
  consultants: [],
  stores: [],
  purchasing: {
    revenue: 0,
    expensesTotal: 0,
    expensesByCategory: {},
    positionsByGroup: [],
    byChannel: {},
    byPurpose: {},
  },
};

function pct(value: number | null): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1).replace(/\.0$/, "")}%`;
}

function upt(value: number | null): string {
  if (value == null) return "—";
  return value.toFixed(2).replace(/\.?0+$/, "") || "0";
}

type PeriodPreset = "day" | "week" | "month" | "custom";

function ymd(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function presetRange(preset: PeriodPreset): { from: string; to: string } {
  const today = new Date();
  const to = ymd(today);
  if (preset === "day") return { from: to, to };
  const from = new Date(today);
  from.setDate(from.getDate() - (preset === "week" ? 6 : 29));
  return { from: ymd(from), to };
}

/** Таблица консультантов/магазинов: одна разметка на оба разреза. */
function StatsTable({ rows, nameLabel }: { rows: StatsRow[]; nameLabel: string }) {
  if (rows.length === 0) return <p className="text-mute text-sm">Нет заявок за период.</p>;
  return (
    <div className="overflow-x-auto -mx-5 px-5">
      <table className="w-full text-sm min-w-[640px]">
        <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
          <tr>
            <th className="text-left py-2 pr-3 font-medium">{nameLabel}</th>
            <th className="text-right py-2 px-2 font-medium">Клиенты</th>
            <th className="text-right py-2 px-2 font-medium">Успех</th>
            <th className="text-right py-2 px-2 font-medium">Выручка, ₽</th>
            <th className="text-right py-2 px-2 font-medium" title="Успехи / (клиенты − не-сливы − добитые сливы)">
              Конверсия
            </th>
            {/* UPT левее среднего чека — порядок по просьбе владельца (созвон 04.09). */}
            <th className="text-right py-2 px-2 font-medium" title="Позиции в чеках / чеки">UPT</th>
            <th className="text-right py-2 px-2 font-medium">Ср. чек, ₽</th>
            <th className="text-right py-2 px-2 font-medium">Сливы</th>
            <th className="text-right py-2 pl-2 font-medium">Не-сливы</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-800">
          {rows.map((r) => (
            <tr key={r.name}>
              <td className="py-2.5 pr-3 text-white font-medium">{r.name}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-mute-soft">{r.clients}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-white">{r.success}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-gold-soft font-semibold">
                {moneyPlain(r.revenue)}
              </td>
              <td className="py-2.5 px-2 text-right tabular-nums text-white">{pct(r.conversion)}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-white">{upt(r.upt)}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-mute-soft">{moneyPlain(r.avgCheck)}</td>
              <td className="py-2.5 px-2 text-right tabular-nums text-mute-soft">
                {r.slivs}
                {r.rescuedSlivs > 0 && (
                  <span className="text-emerald-300/80" title="Добиты в другом магазине">
                    {" "}
                    (−{r.rescuedSlivs})
                  </span>
                )}
              </td>
              <td className="py-2.5 pl-2 text-right tabular-nums text-mute-soft">{r.noSlivs}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Раздел статистики, свёрнутый по умолчанию (созвон 04.09): детализация
 * раскрывается по клику, чтобы экран открывался плитками, а не таблицами.
 */
function CollapsibleSection({
  title,
  icon,
  count,
  children,
}: {
  title: string;
  icon?: ReactNode;
  count: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-left"
      >
        <ChevronRight
          size={16}
          className={`text-mute transition-transform ${open ? "rotate-90" : ""}`}
        />
        {icon}
        <h3 className="text-white font-bold">{title}</h3>
        <span className="chip bg-ink-700 text-mute text-[11px]">{count}</span>
      </button>
      {open && <div className="mt-4">{children}</div>}
    </Card>
  );
}

function CountList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <p className="text-mute text-sm">Нет данных.</p>;
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div className="space-y-2">
      {entries.map(([name, count]) => (
        <div key={name}>
          <div className="flex justify-between text-[13px] mb-1">
            <span className="text-mute-soft">{name}</span>
            <span className="text-white tabular-nums">{count}</span>
          </div>
          <div className="h-1.5 bg-ink-700 rounded-full overflow-hidden">
            <div className="h-full bg-gold rounded-full" style={{ width: `${(count / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function Dashboard() {
  const { user } = useAuth();
  // Консультант видит только свои цифры: сервер и так отдаёт лишь его заявки,
  // здесь убираем блок закупа и правим подпись (созвон 04.09).
  const ownOnly = user?.role === "consultant";
  const [summary, setSummary] = useState<StatsSummary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(!USE_MOCK);
  const [preset, setPreset] = useState<PeriodPreset>("week");
  const [range, setRange] = useState(() => presetRange("week"));

  function applyPreset(next: PeriodPreset) {
    setPreset(next);
    if (next !== "custom") setRange(presetRange(next));
  }

  useEffect(() => {
    if (USE_MOCK) return; // в демо-режиме статистика по мокам не считается — реальных заявок нет
    let alive = true;
    setLoading(true);
    api
      .getQuery<StatsSummary>("/stats/summary", { from: range.from, to: range.to })
      .then((res) => { if (alive) setSummary(res); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [range.from, range.to]);

  const t = summary.totals ?? EMPTY_ROW;
  const p = summary.purchasing ?? EMPTY_SUMMARY.purchasing;
  const expenseEntries = Object.entries(p.expensesByCategory).sort((a, b) => b[1] - a[1]);

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Статистика</h1>
        {loading && <Loader2 size={16} className="text-mute animate-spin" />}
      </div>
      <Hint>
        {ownOnly ? "Только твои заявки. " : ""}
        Формулы сводки: средний чек = выручка / клиенты. Конверсия = успехи / (клиенты − не-сливы −
        сливы, добитые в другом магазине). UPT = позиции в чеках / чеки. Костюм в UPT считается
        парой пиджак плюс брюки, не тремя отдельными позициями.
      </Hint>

      <div className="card p-3 mb-5 flex flex-wrap items-center gap-2">
        <div className="inline-flex border border-ink-700 rounded-lg overflow-hidden">
          {([
            ["day", "День"],
            ["week", "Неделя"],
            ["month", "Месяц"],
            ["custom", "Период"],
          ] as Array<[PeriodPreset, string]>).map(([value, label]) => (
            <button
              key={value}
              onClick={() => applyPreset(value)}
              className={`px-4 py-2 text-sm ${preset === value ? "bg-gold text-ink-950 font-semibold" : "text-mute hover:text-white"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          type="date"
          className="input py-2 text-sm w-auto"
          value={range.from}
          onChange={(e) => {
            setPreset("custom");
            setRange((prev) => ({ ...prev, from: e.target.value }));
          }}
          aria-label="Период с"
        />
        <input
          type="date"
          className="input py-2 text-sm w-auto"
          value={range.to}
          min={range.from}
          onChange={(e) => {
            setPreset("custom");
            setRange((prev) => ({ ...prev, to: e.target.value }));
          }}
          aria-label="Период по"
        />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        <StatTile label="Выручка, ₽" value={moneyPlain(t.revenue)} tone="gold" />
        <StatTile label="Клиенты" value={String(t.clients)} tone="gray" sub={`успехов ${t.success}`} />
        <StatTile label="Конверсия" value={pct(t.conversion)} tone="green" />
        {/* UPT левее среднего чека — порядок по просьбе владельца (созвон 04.09). */}
        <StatTile label="UPT" value={upt(t.upt)} tone="amber" />
        <StatTile label="Средний чек, ₽" value={moneyPlain(t.avgCheck)} tone="blue" />
        <StatTile
          label="Сливы / не-сливы"
          value={`${t.slivs} / ${t.noSlivs}`}
          tone="red"
          sub={t.rescuedSlivs > 0 ? `добито в другом магазине: ${t.rescuedSlivs}` : undefined}
        />
      </div>

      <div className="space-y-4">
        <CollapsibleSection title="Консультанты" count={summary.consultants.length}>
          <StatsTable rows={summary.consultants} nameLabel="Консультант" />
        </CollapsibleSection>

        <CollapsibleSection
          title="Магазины"
          count={summary.stores.length}
          icon={<Store size={18} className="text-gold" />}
        >
          <StatsTable rows={summary.stores} nameLabel="Магазин" />
        </CollapsibleSection>

        {!ownOnly && (
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <ShoppingCart size={18} className="text-gold" />
            <h3 className="text-white font-bold">Продажи и закуп</h3>
          </div>
          <div className="grid sm:grid-cols-3 gap-3 mb-5">
            <StatTile label="Доходы, ₽" value={moneyPlain(p.revenue)} tone="gold" />
            <StatTile label="Расходы, ₽" value={moneyPlain(p.expensesTotal)} tone="red" />
            <StatTile label="Разница, ₽" value={moneyPlain(p.revenue - p.expensesTotal)} tone="green" />
          </div>

          <div className="grid lg:grid-cols-2 gap-6">
            <div>
              <div className="field-label mb-2">Проданные позиции по группам</div>
              {p.positionsByGroup.length === 0 ? (
                <p className="text-mute text-sm">Нет продаж за период.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
                      <tr>
                        <th className="text-left py-2 pr-3 font-medium">Группа</th>
                        <th className="text-right py-2 px-2 font-medium">Шт</th>
                        <th className="text-right py-2 pl-2 font-medium">Выручка, ₽</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-ink-800">
                      {p.positionsByGroup.map((g) => (
                        <tr key={g.group}>
                          <td className="py-2 pr-3 text-mute-soft">{g.group}</td>
                          <td className="py-2 px-2 text-right tabular-nums text-white">{g.qty}</td>
                          <td className="py-2 pl-2 text-right tabular-nums text-gold-soft">
                            {moneyPlain(g.revenue)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="space-y-5">
              <div>
                <div className="field-label mb-2">Расходы по категориям</div>
                {expenseEntries.length === 0 ? (
                  <p className="text-mute text-sm">Нет расходов за период.</p>
                ) : (
                  <div className="space-y-1.5">
                    {expenseEntries.map(([cat, sum]) => (
                      <div key={cat} className="flex justify-between text-[13px]">
                        <span className="text-mute-soft">{cat}</span>
                        <span className="text-white tabular-nums">{moneyPlain(sum)} ₽</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <div className="field-label mb-2">Источники (успешные чеки)</div>
                <CountList data={p.byChannel} />
              </div>
              <div>
                <div className="field-label mb-2">Цели покупки</div>
                <CountList data={p.byPurpose} />
              </div>
            </div>
          </div>
        </Card>
        )}
      </div>
    </div>
  );
}
