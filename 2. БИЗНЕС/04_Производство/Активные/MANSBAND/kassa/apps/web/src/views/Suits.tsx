import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Download, Loader2, Search, Shirt } from "lucide-react";
import { ITEM_LOCATIONS, SUIT_PART_LABEL } from "@kassa/shared";
import type { SuitBreak, SuitCompleteness, SuitPart, StockStats } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, StatTile } from "../components/ui";
import { SuitModelRow } from "../components/SuitModelRow";

/**
 * Костюмы: комплектность, журнал разбитых костюмов и статистика склада.
 *
 * Костюма как товара в МойСкладе нет — есть пиджак, брюки и жилет, связанные
 * характеристикой «Вариация». Экран показывает костюм так, как его видит
 * продавец: цельные, правильные полупарки (размеры расходятся в допуске, но
 * костюм продаётся) и неправильные полупарки, где пары нет.
 *
 * Экран рабочий, а не отчётный: из строки полупарка ставится перемещение
 * парной части, а список выгружается колл-менеджеру на обзвон.
 */

type Tab = "completeness" | "breaks" | "stock";

const WAREHOUSES = ITEM_LOCATIONS.filter((name) => !name.startsWith("СДЭК"));

export function Suits() {
  const [tab, setTab] = useState<Tab>("completeness");

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2.5">
        <Shirt size={20} className="text-gold" />
        <h1 className="text-xl font-bold text-white">Костюмы</h1>
      </div>
      <div className="flex gap-2 flex-wrap">
        <TabButton active={tab === "completeness"} onClick={() => setTab("completeness")}>
          Комплектность
        </TabButton>
        <TabButton active={tab === "breaks"} onClick={() => setTab("breaks")}>
          Разбитые костюмы
        </TabButton>
        <TabButton active={tab === "stock"} onClick={() => setTab("stock")}>
          Статистика склада
        </TabButton>
      </div>
      {tab === "completeness" && <CompletenessTab />}
      {tab === "breaks" && <BreaksTab />}
      {tab === "stock" && <StockTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`chip px-3 py-1.5 text-[13px] font-semibold transition ${
        active ? "bg-gold text-ink-950" : "bg-ink-800 text-mute hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

function CompletenessTab() {
  const { user } = useAuth();
  const consultant = user?.role === "consultant";
  const [warehouse, setWarehouse] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<SuitCompleteness | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (USE_MOCK) return;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (warehouse) params.set("warehouse", warehouse);
    if (query) params.set("q", query);
    params.set("limit", "200");
    api
      .get<SuitCompleteness>(`/suits/completeness?${params.toString()}`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить костюмы"))
      .finally(() => setLoading(false));
  }, [warehouse, query]);

  async function exportHalfSets() {
    const params = new URLSearchParams();
    if (warehouse) params.set("warehouse", warehouse);
    if (query) params.set("q", query);
    const res = await api.get<{
      rows: Array<{
        variation: string;
        title: string;
        size: string;
        part: SuitPart;
        qty: number;
        missing: SuitPart[];
        nearestSizes: string[];
      }>;
    }>(`/suits/half-sets?${params.toString()}`);
    const header = "Вариация;Костюм;Размер;Часть;Штук;Нет части;Ближайшие размеры";
    const lines = res.rows.map((r) =>
      [
        r.variation,
        r.title,
        r.size,
        SUIT_PART_LABEL[r.part],
        r.qty,
        r.missing.map((p) => SUIT_PART_LABEL[p]).join(" и "),
        r.nearestSizes.join(" "),
      ].join(";")
    );
    // Точка с запятой и BOM — чтобы Excel открыл файл сразу колонками.
    const blob = new Blob([`\uFEFF${[header, ...lines].join("\n")}`], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `полупарки_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const totals = data?.totals;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Цельных костюмов" value={String(totals?.whole ?? 0)} />
        <StatTile
          label="Правильных полупарков"
          value={String(totals?.tolerant ?? 0)}
          sub={`допуск ±${data?.tolerance ?? 0} размера`}
        />
        <StatTile label="Неправильных полупарков" value={String(totals?.orphans ?? 0)} />
        <StatTile
          label="Моделей"
          value={String(totals?.models ?? 0)}
          sub={`${totals?.items ?? 0} изделий`}
        />
      </div>

      <Card className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
          <label className="relative block">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
            <input
              className="input pl-9"
              placeholder="Вариация, цвет, название костюма"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          {!consultant && (
            <select
              className="input"
              value={warehouse}
              onChange={(e) => setWarehouse(e.target.value)}
            >
              <option value="">Все склады, кроме полупарков</option>
              {WAREHOUSES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          )}
          <Button variant="outline" onClick={() => void exportHalfSets()} disabled={USE_MOCK}>
            <Download size={15} /> Полупарки в файл
          </Button>
        </div>
        {consultant && (
          <div className="hint-only text-[12px] text-mute">Комплектность своего магазина.</div>
        )}
        {error && <div className="text-[12px] text-red-300">{error}</div>}
        {USE_MOCK && (
          <div className="text-[12px] text-mute">
            Демо-режим: комплектность считается на живых остатках МойСклад.
          </div>
        )}
      </Card>

      {loading && (
        <div className="flex items-center gap-2 text-mute text-sm">
          <Loader2 size={16} className="animate-spin" /> Считаю комплектность
        </div>
      )}

      <div className="space-y-2">
        {(data?.models ?? []).map((model) => (
          <SuitModelRow
            key={model.modelId}
            model={model}
            open={open === model.modelId}
            onToggle={() => setOpen(open === model.modelId ? null : model.modelId)}
            warehouse={warehouse}
          />
        ))}
        {!loading && (data?.models?.length ?? 0) === 0 && !USE_MOCK && (
          <div className="text-sm text-mute">Ничего не нашлось по этому фильтру.</div>
        )}
      </div>
    </div>
  );
}

export function BreaksTab() {
  const { user } = useAuth();
  const canSnapshot = user?.role === "rop" || user?.role === "admin";
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(today);
  const [rows, setRows] = useState<SuitBreak[]>([]);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    if (USE_MOCK) return;
    setLoading(true);
    try {
      const res = await api.get<{ rows: SuitBreak[] }>(
        `/suits/breaks?from=${from}&to=${to}`
      );
      setRows(res.rows);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  const byConsultant = useMemo(() => {
    const map = new Map<string, number>();
    for (const row of rows) map.set(row.consultant, (map.get(row.consultant) ?? 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[auto_auto_1fr]">
          <label>
            <div className="field-label">С даты</div>
            <input
              type="date"
              className="input"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
          <label>
            <div className="field-label">По дату</div>
            <input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          {canSnapshot && (
            <div className="flex items-end">
              <Button
                variant="outline"
                disabled={USE_MOCK}
                onClick={async () => {
                  const res = await api.post<{ added: number }>("/suits/breaks/snapshot", {});
                  setNote(`Снимок комплектности: добавлено записей ${res.added}`);
                  void load();
                }}
              >
                <CalendarClock size={15} /> Снимок комплектности
              </Button>
            </div>
          )}
        </div>
        <div className="hint-only text-[12px] text-mute">
          Запись появляется в момент продажи части костюма без пары. Снимок сверяет остатки и
          ловит разбиение, которое прошло не через кассу.
        </div>
        {note && <div className="text-[12px] text-emerald-300">{note}</div>}
      </Card>

      {byConsultant.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {byConsultant.map(([name, count]) => (
            <span key={name} className="chip bg-ink-800 text-mute-soft">
              {name}: {count}
            </span>
          ))}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-mute text-sm">
          <Loader2 size={16} className="animate-spin" /> Загружаю журнал
        </div>
      )}

      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.id} className="card p-4 space-y-1">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-white font-semibold">{row.title}</span>
              <span className="text-mute">{row.variation}</span>
              {row.size && <span className="text-mute">размер {row.size}</span>}
              {row.source === "snapshot" && (
                <span className="chip bg-ink-800 text-mute text-[11px]">снимок</span>
              )}
            </div>
            <div className="text-[12px] text-mute">
              {new Date(row.at).toLocaleString("ru-RU")} · {row.store} · {row.consultant}
              {row.refDealNumber ? ` · заявка #${row.refDealNumber}` : ""}
            </div>
            <div className="text-[12px] text-red-200">
              продан {SUIT_PART_LABEL[row.soldPart]}, без пары остались{" "}
              {row.leftParts.map((part) => SUIT_PART_LABEL[part]).join(", ") || "—"}
            </div>
          </div>
        ))}
        {!loading && rows.length === 0 && (
          <div className="text-sm text-mute">За период разбитых костюмов нет.</div>
        )}
      </div>
    </div>
  );
}

export function StockTab() {
  const { user } = useAuth();
  const consultant = user?.role === "consultant";
  const [warehouse, setWarehouse] = useState("");
  const [data, setData] = useState<StockStats | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (USE_MOCK) return;
    setLoading(true);
    const params = new URLSearchParams();
    if (warehouse) params.set("warehouse", warehouse);
    api
      .get<StockStats>(`/suits/stock-stats?${params.toString()}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [warehouse]);

  const lightLabel: Record<StockStats["light"], string> = {
    green: "Зелёный",
    yellow: "Жёлтый",
    red: "Красный",
  };

  return (
    <div className="space-y-4">
      {!consultant && (
        <Card>
          <select className="input" value={warehouse} onChange={(e) => setWarehouse(e.target.value)}>
            <option value="">Все склады, кроме полупарков</option>
            {WAREHOUSES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Card>
      )}
      {loading && (
        <div className="flex items-center gap-2 text-mute text-sm">
          <Loader2 size={16} className="animate-spin" /> Считаю возраст остатка
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(data?.buckets ?? []).map((bucket) => (
          <StatTile
            key={bucket.bucket}
            label={`На складе ${bucket.bucket}`}
            value={String(bucket.items)}
            sub={`${bucket.models} моделей`}
          />
        ))}
      </div>
      <Card className="space-y-2 text-[13px]">
        <div className="text-white font-semibold">Светофор: {lightLabel[data?.light ?? "green"]}</div>
        <div className="text-mute">
          Пороги возраста задаются в настройках; нормативы называет владелец, в коде только механика.
        </div>
        <div className="text-mute">
          Самый старый приход:{" "}
          {data?.oldestEnterDate
            ? new Date(data.oldestEnterDate).toLocaleDateString("ru-RU")
            : "нет данных по оприходованиям"}
        </div>
        <div className="text-mute">
          Перекос сеток: пиджаков без брюк своего размера {data?.gridSkew.jacketsWithoutTrousers ?? 0},
          брюк без пиджаков {data?.gridSkew.trousersWithoutJackets ?? 0}.
        </div>
        <div className="text-[12px] text-mute/70">
          Оборачиваемость и неликвид появятся после накопления продаж: в МойСкладе розничной
          истории пока нет, касса начнёт её писать с запуска.
        </div>
      </Card>
    </div>
  );
}
