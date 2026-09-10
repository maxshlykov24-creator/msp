import { useEffect, useState } from "react";
import { SlidersHorizontal, Plus, Trash2, Check } from "lucide-react";
import { api, USE_MOCK } from "../api/client";
import { Button } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { invalidateAppSettings } from "../lib/appSettings";
import { Hint } from "../lib/hints";
import { SUIT_PRICE_RULES_DEFAULT } from "@kassa/shared";

interface PriceAudit {
  total: number;
  withoutPrice: number;
  updatedLast24h: number;
  lastUpdatedAt: string | null;
  samples: Array<{ name: string; article: string | null; category: string | null }>;
}

/**
 * Настройки мотивации и кассы (созвон 20.08, П1 + П5):
 * порог чека для САР и конструктор порогов ЗП. Доступ — rop / admin.
 */

interface Tier {
  from: number;
  to: number | null;
  /** Премия либо штраф в процентах от выручки за период (созвон 04.09). */
  bonusPct: number;
}

interface PayrollSettings {
  revenuePct: number;
  dailyFloor: number;
  conversionTiers: Tier[];
  uptTiers: Tier[];
  penaltyConversionTiers: Tier[];
  penaltyUptTiers: Tier[];
  updatedBy?: string;
  updatedAt?: string;
}

interface TierRow {
  id: string;
  from: string;
  to: string;
  bonusPct: string;
}

function toRows(tiers: Tier[] | undefined): TierRow[] {
  return (tiers ?? []).map((t) => ({
    id: crypto.randomUUID(),
    from: String(t.from),
    to: t.to == null ? "" : String(t.to),
    bonusPct: String(t.bonusPct ?? 0),
  }));
}

function fromRows(rows: TierRow[]): Tier[] {
  return rows
    .filter((r) => r.from !== "" && r.bonusPct !== "")
    .map((r) => ({
      from: Number(r.from),
      to: r.to === "" ? null : Number(r.to),
      bonusPct: Number(r.bonusPct),
    }));
}

function TierEditor({
  label,
  hint,
  rows,
  setRows,
  valueLabel = "премия, % от выручки",
}: {
  label: string;
  hint: string;
  rows: TierRow[];
  setRows: (fn: (prev: TierRow[]) => TierRow[]) => void;
  valueLabel?: string;
}) {
  return (
    <div>
      <div className="field-label">{label}</div>
      <div className="hint-only text-[12px] text-mute mb-2">{hint}</div>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.id} className="flex gap-2 items-center">
            <input
              className="input w-[90px]"
              inputMode="decimal"
              placeholder="от"
              value={row.from}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) => (r.id === row.id ? { ...r, from: e.target.value.replace(",", ".") } : r))
                )
              }
            />
            <span className="text-mute text-sm">—</span>
            <input
              className="input w-[90px]"
              inputMode="decimal"
              placeholder="до (пусто = ∞)"
              value={row.to}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) => (r.id === row.id ? { ...r, to: e.target.value.replace(",", ".") } : r))
                )
              }
            />
            <input
              className="input flex-1"
              inputMode="decimal"
              placeholder={valueLabel}
              value={row.bonusPct}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) =>
                    r.id === row.id
                      ? { ...r, bonusPct: e.target.value.replace(",", ".").replace(/[^\d.]/g, "") }
                      : r
                  )
                )
              }
            />
            <button
              type="button"
              className="text-mute hover:text-white"
              onClick={() => setRows((prev) => prev.filter((r) => r.id !== row.id))}
            >
              <Trash2 size={16} />
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() =>
          setRows((prev) => [...prev, { id: crypto.randomUUID(), from: "", to: "", bonusPct: "" }])
        }
        className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-gold-soft hover:text-white"
      >
        <Plus size={15} /> Добавить порог
      </button>
    </div>
  );
}

function ymd(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Ручной расчёт ЗП за выбранный период (созвон 04.09): не ждать вторничной
 * автоматики. Результат — задача Эдвину «Выдать зарплату» с разбивкой по людям.
 */
function PayrollEnqueueCard() {
  const today = new Date();
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 6);
  const [from, setFrom] = useState(ymd(weekAgo));
  const [to, setTo] = useState(ymd(today));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setError(null);
    setResult(null);
    if (from > to) return setError("Начало периода позже конца");
    setBusy(true);
    try {
      const res = await api.post<{ created: boolean; total: number; consultants: number }>(
        "/payroll/enqueue",
        { from, to }
      );
      setResult(
        res.created
          ? `Задача Эдвину создана: ${res.consultants} чел., ${res.total.toLocaleString("ru-RU")} ₽`
          : "Такая задача за этот период уже в очереди Эдвина"
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось поставить задачу");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4 space-y-3">
      <div>
        <div className="text-white font-semibold">Расчёт ЗП за период</div>
        <div className="hint-only text-[12px] text-mute mt-1">
          Автоматика считает по вторникам за прошлую неделю. Здесь — расчёт за любой период
          и задача Эдвину «Выдать зарплату».
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label>
          <div className="field-label">С</div>
          <input
            type="date"
            className="input py-2 w-auto"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label>
          <div className="field-label">По</div>
          <input
            type="date"
            className="input py-2 w-auto"
            value={to}
            min={from}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <Button variant="subtle" disabled={busy || USE_MOCK} onClick={() => void run()}>
          {busy ? "Считаем…" : "Посчитать и поставить задачу"}
        </Button>
      </div>
      {result && <div className="text-[13px] text-emerald-300">{result}</div>}
      {error && <div className="text-[13px] text-red-300">{error}</div>}
    </div>
  );
}

/**
 * Сверка прайса после обновления цен в МойСклад (созвон 04.09, п.7). Цены в кассу
 * приходят только синком, редактировать их здесь нельзя — можно проверить, что
 * новый прайс доехал и нет позиций без цены.
 */
function PriceAuditCard() {
  const [data, setData] = useState<PriceAudit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      setData(await api.get<PriceAudit>("/admin/prices/audit"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось получить сверку");
    }
  }

  async function sync() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/admin/sync/products", {});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Синк не прошёл");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (USE_MOCK) return;
    void load();
  }, []);

  return (
    <div className="card p-4 space-y-3">
      <div>
        <div className="text-white font-semibold">Цены и синк номенклатуры</div>
        <div className="hint-only text-[12px] text-mute mt-1">
          Прайс правится в МойСклад. После обновления прогони синк и проверь, что позиций без цены нет.
        </div>
      </div>
      {data && (
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Позиций" value={data.total.toLocaleString("ru-RU")} />
          <Stat
            label="Без цены"
            value={data.withoutPrice.toLocaleString("ru-RU")}
            warn={data.withoutPrice > 0}
          />
          <Stat label="Обновлено за сутки" value={data.updatedLast24h.toLocaleString("ru-RU")} />
        </div>
      )}
      {data && data.samples.length > 0 && (
        <div className="text-[12px] text-mute">
          <div className="mb-1">Проверить в МойСклад:</div>
          <ul className="space-y-0.5">
            {data.samples.slice(0, 10).map((row) => (
              <li key={`${row.article ?? ""}${row.name}`} className="text-white/80">
                {row.name}
                {row.article && <span className="text-mute"> · {row.article}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="subtle" disabled={busy || USE_MOCK} onClick={() => void sync()}>
          {busy ? "Синхронизируем…" : "Прогнать синк номенклатуры"}
        </Button>
        {data?.lastUpdatedAt && (
          <span className="text-[12px] text-mute">
            последнее обновление {new Date(data.lastUpdatedAt).toLocaleString("ru-RU")}
          </span>
        )}
      </div>
      {error && <div className="text-[13px] text-red-300">{error}</div>}
    </div>
  );
}

interface SuitPriceRow {
  id: string;
  label: string;
  priceRub: string;
  active: boolean;
}

function suitPriceRowsFromOverrides(
  overrides: Record<string, { priceRub: number; active: boolean }> | undefined
): SuitPriceRow[] {
  return SUIT_PRICE_RULES_DEFAULT.map((rule) => {
    const o = overrides?.[rule.id];
    return {
      id: rule.id,
      label: rule.label,
      priceRub: String(o?.priceRub ?? rule.priceRub),
      active: o?.active ?? rule.active,
    };
  });
}

function suitPriceOverridesFromRows(
  rows: SuitPriceRow[]
): Record<string, { priceRub: number; active: boolean }> {
  const result: Record<string, { priceRub: number; active: boolean }> = {};
  for (const row of rows) {
    const price = Number(row.priceRub);
    if (!Number.isFinite(price) || price < 0) continue;
    result[row.id] = { priceRub: Math.round(price), active: row.active };
  }
  return result;
}

/**
 * Матрица цен костюмов (созвон 09.09): логика подбора правила (группа МойСклад,
 * вид пиджака, ростовка, число предметов) зашита в коде —
 * `packages/shared/src/suitPrices.ts`. Здесь правится только цена и активность
 * каждого правила, без риска сломать матчинг. Правило неактивно — цена костюма
 * не определяется, части идут по своим ценам МойСклад.
 */
function SuitPriceMatrixCard({
  rows,
  setRows,
}: {
  rows: SuitPriceRow[];
  setRows: (fn: (prev: SuitPriceRow[]) => SuitPriceRow[]) => void;
}) {
  return (
    <div className="card p-4 space-y-3">
      <div>
        <div className="text-white font-semibold">Матрица цен костюмов</div>
        <div className="hint-only text-[12px] text-mute mt-1">
          Цена костюма в кассе при сборке пиджака и брюк (+ жилета) одной вариации. Правило,
          какую строку подобрать, — в коде; тут только цена и включённость.
        </div>
      </div>
      <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
        {rows.map((row) => (
          <div key={row.id} className="flex gap-2 items-center">
            <label className="flex items-center gap-1.5 shrink-0">
              <input
                type="checkbox"
                checked={row.active}
                onChange={(e) =>
                  setRows((prev) =>
                    prev.map((r) => (r.id === row.id ? { ...r, active: e.target.checked } : r))
                  )
                }
              />
            </label>
            <div className="flex-1 text-[13px] text-white/90 break-words">{row.label}</div>
            <input
              className="input w-28 py-1.5 text-[13px] shrink-0"
              inputMode="numeric"
              value={row.priceRub}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) =>
                    r.id === row.id ? { ...r, priceRub: e.target.value.replace(/\D/g, "") } : r
                  )
                )
              }
            />
            <span className="text-[12px] text-mute shrink-0">₽</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="rounded-lg border border-ink-700 px-3 py-2">
      <div className="text-[11px] text-mute uppercase">{label}</div>
      <div className={`text-lg font-bold ${warn ? "text-red-300" : "text-white"}`}>{value}</div>
    </div>
  );
}

export function SettingsScreen() {
  const { user } = useAuth();
  const allowed = user?.role === "rop" || user?.role === "admin";

  const [saryMinCheck, setSaryMinCheck] = useState("");
  // Группы вводятся построчно: так проще править список веток МойСклад.
  const [suitGroups, setSuitGroups] = useState("");
  const [revenuePct, setRevenuePct] = useState("");
  const [dailyFloor, setDailyFloor] = useState("");
  const [conversionRows, setConversionRows] = useState<TierRow[]>([]);
  const [uptRows, setUptRows] = useState<TierRow[]>([]);
  const [penaltyConversionRows, setPenaltyConversionRows] = useState<TierRow[]>([]);
  const [penaltyUptRows, setPenaltyUptRows] = useState<TierRow[]>([]);
  const [suitPriceRows, setSuitPriceRows] = useState<SuitPriceRow[]>(() =>
    suitPriceRowsFromOverrides(undefined)
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ updatedBy?: string; updatedAt?: string }>({});

  useEffect(() => {
    if (!allowed || USE_MOCK) {
      setLoading(false);
      return;
    }
    Promise.all([
      api.get<{
        saryMinCheck: number;
        sarySuitGroups: string[];
        suitPriceOverrides?: Record<string, { priceRub: number; active: boolean }>;
      }>("/settings"),
      api.get<PayrollSettings>("/payroll/settings"),
    ])
      .then(([app, payroll]) => {
        setSaryMinCheck(String(app.saryMinCheck));
        setSuitGroups((app.sarySuitGroups ?? []).join("\n"));
        setSuitPriceRows(suitPriceRowsFromOverrides(app.suitPriceOverrides));
        setRevenuePct(String(payroll.revenuePct));
        setDailyFloor(String(payroll.dailyFloor));
        setConversionRows(toRows(payroll.conversionTiers));
        setUptRows(toRows(payroll.uptTiers));
        setPenaltyConversionRows(toRows(payroll.penaltyConversionTiers));
        setPenaltyUptRows(toRows(payroll.penaltyUptTiers));
        setMeta({ updatedBy: payroll.updatedBy, updatedAt: payroll.updatedAt });
      })
      .catch(() => setError("Не удалось загрузить настройки"))
      .finally(() => setLoading(false));
  }, [allowed]);

  async function save() {
    setError(null);
    setSaved(false);
    const minCheck = Number(saryMinCheck);
    const pct = Number(revenuePct.replace(",", "."));
    const floor = Number(dailyFloor);
    const groups = suitGroups
      .split("\n")
      .map((g) => g.trim())
      .filter(Boolean);
    if (!minCheck || minCheck < 0) return setError("Порог чека САР должен быть числом больше нуля");
    if (groups.length === 0) return setError("Укажите хотя бы одну группу костюмов");
    if (!pct || pct <= 0 || pct > 100) return setError("Процент от выручки: число от 0 до 100");
    if (!floor || floor < 0) return setError("Обеспечительная ставка должна быть числом больше нуля");
    if (suitPriceRows.some((r) => r.active && (!r.priceRub || Number(r.priceRub) <= 0))) {
      return setError("У включённого правила матрицы костюмов должна быть цена больше нуля");
    }
    setSaving(true);
    try {
      if (!USE_MOCK) {
        await api.put("/settings", {
          saryMinCheck: minCheck,
          sarySuitGroups: groups,
          suitPriceOverrides: suitPriceOverridesFromRows(suitPriceRows),
        });
        await api.put("/payroll/settings", {
          revenuePct: pct,
          dailyFloor: floor,
          conversionTiers: fromRows(conversionRows),
          uptTiers: fromRows(uptRows),
          penaltyConversionTiers: fromRows(penaltyConversionRows),
          penaltyUptTiers: fromRows(penaltyUptRows),
        });
      }
      invalidateAppSettings();
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  }

  if (!allowed) {
    return (
      <div className="max-w-2xl mx-auto card py-10 text-center text-mute">
        Настройки мотивации доступны только РОПу и администратору.
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Настройки мотивации</h1>
      </div>
      <Hint>
        Порог чека для САР и правила расчёта зарплаты. Условия единые для всех консультантов.
        Доступ — РОП и администратор.
      </Hint>

      {loading ? (
        <div className="card py-10 text-center text-mute">Загружаем настройки…</div>
      ) : (
        <div className="space-y-4">
          <div className="card p-4 space-y-3">
            <div className="text-white font-semibold">САР</div>
            <label className="block">
              <div className="field-label">Минимальная сумма чека для САР, ₽</div>
              <input
                className="input"
                inputMode="numeric"
                value={saryMinCheck}
                onChange={(e) => setSaryMinCheck(e.target.value.replace(/\D/g, ""))}
              />
              <div className="hint-only text-[12px] text-mute mt-1">
                Порог работает, только когда костюмов в чеке нет: тогда САР одна и лишь при чеке
                от этой суммы.
              </div>
            </label>
            <label className="block">
              <div className="field-label">Группы МойСклад, которые считаются костюмом</div>
              <textarea
                className="input min-h-[92px]"
                value={suitGroups}
                onChange={(e) => setSuitGroups(e.target.value)}
                placeholder={"Костюмы\nПолупарки"}
              />
              <div className="hint-only text-[12px] text-mute mt-1">
                Одна группа в строке, сравнение по началу пути: «Костюмы» покрывает и
                «Костюмы/Тройки». Сколько костюмов в чеке — столько САР.
              </div>
            </label>
          </div>

          <div className="card p-4 space-y-4">
            <div className="text-white font-semibold">Зарплата за день</div>
            <div className="grid sm:grid-cols-2 gap-3">
              <label>
                <div className="field-label">Процент от выручки, %</div>
                <input
                  className="input"
                  inputMode="decimal"
                  value={revenuePct}
                  onChange={(e) => setRevenuePct(e.target.value)}
                />
              </label>
              <label>
                <div className="field-label">Обеспечительная ставка, ₽/день</div>
                <input
                  className="input"
                  inputMode="numeric"
                  value={dailyFloor}
                  onChange={(e) => setDailyFloor(e.target.value.replace(/\D/g, ""))}
                />
              </label>
            </div>
            <div className="hint-only text-[12px] text-mute">
              За день платится максимум из «% от выручки» и ставки. Рабочий день — день, когда у
              консультанта есть хотя бы одна заявка.
            </div>
          </div>

          <div className="card p-4 space-y-5">
            <div>
              <div className="text-white font-semibold">Премии за период</div>
              <div className="hint-only text-[12px] text-mute mt-1">
                Премия считается процентом от выручки консультанта за период, а не суммой.
              </div>
            </div>
            <TierEditor
              label="Пороги по конверсии, %"
              hint="Граница «от» включительно, «до» исключительно: 87 ровно и 87,001 — разные разделы."
              rows={conversionRows}
              setRows={setConversionRows}
            />
            <TierEditor
              label="Пороги по UPT"
              hint="UPT = позиций в чеках / количество чеков за период."
              rows={uptRows}
              setRows={setUptRows}
            />
          </div>

          <div className="card p-4 space-y-5">
            <div>
              <div className="text-white font-semibold">Штрафы за период</div>
              <div className="hint-only text-[12px] text-mute mt-1">
                Тот же конструктор, но процент вычитается из итога. Пусто — штрафов нет.
              </div>
            </div>
            <TierEditor
              label="Пороги по конверсии, %"
              hint="Например: конверсия ниже 85% — минус процент от выручки."
              rows={penaltyConversionRows}
              setRows={setPenaltyConversionRows}
              valueLabel="штраф, % от выручки"
            />
            <TierEditor
              label="Пороги по UPT"
              hint="Например: UPT ниже 1,2 — минус процент от выручки."
              rows={penaltyUptRows}
              setRows={setPenaltyUptRows}
              valueLabel="штраф, % от выручки"
            />
          </div>

          <PayrollEnqueueCard />

          <PriceAuditCard />

          <SuitPriceMatrixCard rows={suitPriceRows} setRows={setSuitPriceRows} />

          {meta.updatedAt && (
            <div className="text-[12px] text-mute">
              Последнее изменение: {new Date(meta.updatedAt).toLocaleString("ru-RU")}
              {meta.updatedBy ? ` · ${meta.updatedBy}` : ""}
            </div>
          )}

          {error && (
            <div className="text-[13px] text-red-300 bg-red-400/10 border border-red-400/30 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
          {saved && !error && (
            <div className="text-[13px] text-emerald-300 bg-emerald-400/10 border border-emerald-400/30 rounded-lg px-3 py-2">
              Настройки сохранены
            </div>
          )}

          <div className="flex justify-end">
            <Button disabled={saving} onClick={() => void save()}>
              <Check size={15} /> {saving ? "Сохраняем…" : "Сохранить настройки"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
