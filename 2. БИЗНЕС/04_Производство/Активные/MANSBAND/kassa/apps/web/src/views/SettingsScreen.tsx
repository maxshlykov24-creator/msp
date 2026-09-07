import { useEffect, useState } from "react";
import { SlidersHorizontal, Plus, Trash2, Check } from "lucide-react";
import { api, USE_MOCK } from "../api/client";
import { Button } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { invalidateAppSettings } from "../lib/appSettings";

/**
 * Настройки мотивации и кассы (созвон 20.08, П1 + П5):
 * порог чека для САР и конструктор порогов ЗП. Доступ — rop / admin.
 */

interface Tier {
  from: number;
  to: number | null;
  bonus: number;
}

interface PayrollSettings {
  revenuePct: number;
  dailyFloor: number;
  conversionTiers: Tier[];
  uptTiers: Tier[];
  updatedBy?: string;
  updatedAt?: string;
}

interface TierRow {
  id: string;
  from: string;
  to: string;
  bonus: string;
}

function toRows(tiers: Tier[]): TierRow[] {
  return tiers.map((t) => ({
    id: crypto.randomUUID(),
    from: String(t.from),
    to: t.to == null ? "" : String(t.to),
    bonus: String(t.bonus),
  }));
}

function fromRows(rows: TierRow[]): Tier[] {
  return rows
    .filter((r) => r.from !== "" && r.bonus !== "")
    .map((r) => ({
      from: Number(r.from),
      to: r.to === "" ? null : Number(r.to),
      bonus: Number(r.bonus),
    }));
}

function TierEditor({
  label,
  hint,
  rows,
  setRows,
}: {
  label: string;
  hint: string;
  rows: TierRow[];
  setRows: (fn: (prev: TierRow[]) => TierRow[]) => void;
}) {
  return (
    <div>
      <div className="field-label">{label}</div>
      <div className="text-[12px] text-mute mb-2">{hint}</div>
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
              inputMode="numeric"
              placeholder="премия, ₽"
              value={row.bonus}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) => (r.id === row.id ? { ...r, bonus: e.target.value.replace(/\D/g, "") } : r))
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
          setRows((prev) => [...prev, { id: crypto.randomUUID(), from: "", to: "", bonus: "" }])
        }
        className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-gold-soft hover:text-white"
      >
        <Plus size={15} /> Добавить порог
      </button>
    </div>
  );
}

export function SettingsScreen() {
  const { user } = useAuth();
  const allowed = user?.role === "rop" || user?.role === "admin";

  const [saryMinCheck, setSaryMinCheck] = useState("");
  const [revenuePct, setRevenuePct] = useState("");
  const [dailyFloor, setDailyFloor] = useState("");
  const [conversionRows, setConversionRows] = useState<TierRow[]>([]);
  const [uptRows, setUptRows] = useState<TierRow[]>([]);
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
      api.get<{ saryMinCheck: number }>("/settings"),
      api.get<PayrollSettings>("/payroll/settings"),
    ])
      .then(([app, payroll]) => {
        setSaryMinCheck(String(app.saryMinCheck));
        setRevenuePct(String(payroll.revenuePct));
        setDailyFloor(String(payroll.dailyFloor));
        setConversionRows(toRows(payroll.conversionTiers));
        setUptRows(toRows(payroll.uptTiers));
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
    if (!minCheck || minCheck < 0) return setError("Порог чека САР должен быть числом больше нуля");
    if (!pct || pct <= 0 || pct > 100) return setError("Процент от выручки: число от 0 до 100");
    if (!floor || floor < 0) return setError("Обеспечительная ставка должна быть числом больше нуля");
    setSaving(true);
    try {
      if (!USE_MOCK) {
        await api.put("/settings", { saryMinCheck: minCheck });
        await api.put("/payroll/settings", {
          revenuePct: pct,
          dailyFloor: floor,
          conversionTiers: fromRows(conversionRows),
          uptTiers: fromRows(uptRows),
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
      <p className="text-mute text-sm mb-5">
        Порог чека для САР и правила расчёта зарплаты. Условия единые для всех консультантов.
      </p>

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
              <div className="text-[12px] text-mute mt-1">
                Чек ниже порога — САР не начисляется и бонус в форме продажи недоступен.
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
            <div className="text-[12px] text-mute">
              За день платится максимум из «% от выручки» и ставки. Рабочий день — день, когда у
              консультанта есть хотя бы одна заявка.
            </div>
          </div>

          <div className="card p-4 space-y-5">
            <div className="text-white font-semibold">Премии за период</div>
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
