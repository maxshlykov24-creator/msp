import { useEffect, useState } from "react";
import { BarChart3, Store, Loader2 } from "lucide-react";
import { StatTile, Card } from "../components/ui";
import { moneyPlain } from "../lib/format";
import { api, USE_MOCK } from "../api/client";

interface KpiSummary {
  totalDeals: number;
  totalRevenue: number;
  successCount: number;
  conversion: number;
  avgCheck: number;
  byStore: Record<string, { count: number; revenue: number }>;
  byConsultant: Record<string, { count: number; revenue: number }>;
}

const EMPTY_SUMMARY: KpiSummary = {
  totalDeals: 0,
  totalRevenue: 0,
  successCount: 0,
  conversion: 0,
  avgCheck: 0,
  byStore: {},
  byConsultant: {},
};

function Bar({ value, max, tone = "bg-gold" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="h-2 bg-ink-700 rounded-full overflow-hidden">
      <div className={`h-full ${tone} rounded-full`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Dashboard() {
  const [summary, setSummary] = useState<KpiSummary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(!USE_MOCK);

  useEffect(() => {
    if (USE_MOCK) return; // в демо-режиме статистика по мокам не считается — реальных заявок нет
    let alive = true;
    setLoading(true);
    api
      .get<KpiSummary>("/stats/summary")
      .then((res) => { if (alive) setSummary(res); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const byConsultant = Object.entries(summary.byConsultant).sort((a, b) => b[1].revenue - a[1].revenue);
  const byStore = Object.entries(summary.byStore).sort((a, b) => b[1].revenue - a[1].revenue);
  const maxConsultantRev = Math.max(1, ...byConsultant.map(([, v]) => v.revenue));
  const maxStoreRev = Math.max(1, ...byStore.map(([, v]) => v.revenue));

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Статистика</h1>
        {loading && <Loader2 size={16} className="text-mute animate-spin" />}
      </div>
      <p className="text-mute text-sm mb-5">
        По заявкам, проведённым через кассу (этап «Успех»). Данные копятся с момента запуска.
      </p>

      <div className="grid sm:grid-cols-4 gap-3 mb-6">
        <StatTile label="Выручка, ₽" value={moneyPlain(summary.totalRevenue)} tone="gold" />
        <StatTile label="Заявок всего" value={String(summary.totalDeals)} tone="gray" />
        <StatTile label="Конверсия" value={`${Math.round(summary.conversion * 100)}%`} tone="green" />
        <StatTile label="Средний чек, ₽" value={moneyPlain(summary.avgCheck)} tone="blue" />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="text-white font-bold mb-4">Консультанты — выручка, ₽</h3>
          {byConsultant.length === 0 ? (
            <p className="text-mute text-sm">Нет проведённых заявок.</p>
          ) : (
            <div className="space-y-4">
              {byConsultant.map(([name, v]) => (
                <div key={name}>
                  <div className="flex justify-between text-sm mb-1.5">
                    <span className="text-white font-medium">{name}</span>
                    <span className="text-mute">{moneyPlain(v.revenue)} · <span className="text-white">{v.count} шт</span></span>
                  </div>
                  <Bar value={v.revenue} max={maxConsultantRev} />
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Store size={18} className="text-gold" />
            <h3 className="text-white font-bold">По магазинам, ₽</h3>
          </div>
          {byStore.length === 0 ? (
            <p className="text-mute text-sm">Нет проведённых заявок.</p>
          ) : (
            <div className="space-y-4">
              {byStore.map(([name, v]) => (
                <div key={name}>
                  <div className="flex justify-between text-sm mb-1.5">
                    <span className="text-white">{name}</span>
                    <span className="text-mute">{moneyPlain(v.revenue)} · {v.count} шт</span>
                  </div>
                  <Bar value={v.revenue} max={maxStoreRev} tone="bg-gold-dim" />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
