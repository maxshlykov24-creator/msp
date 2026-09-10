import { useState, useMemo, useEffect } from "react";
import { Ticket, Wallet, Search, Filter, RefreshCw, Upload } from "lucide-react";
import { useStore } from "../store";
import { money, dateRu } from "../lib/format";
import { Badge, Button, Modal, Field, StatTile } from "../components/ui";
import type { Certificate } from "../data/types";
import { api, USE_MOCK } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Hint } from "../lib/hints";

interface ImportPreview {
  valid: number;
  conflicts: string[];
  errors: Array<{ line: number; message: string }>;
  imported: number;
  dryRun: boolean;
}

const statusTone = { active: "green", used: "gray", expired: "red" } as const;
const statusLabel = { active: "Активен", used: "Использован", expired: "Просрочен" } as const;

export function Certificates() {
  const { certificates, redeemCertificate } = useStore();
  const { user } = useAuth();
  const [redeem, setRedeem] = useState<Certificate | null>(null);
  const [amount, setAmount] = useState("");
  const [q, setQ] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [status, setStatus] = useState("all");
  const [type, setType] = useState("all");
  const [refreshing, setRefreshing] = useState(false);
  const [list, setList] = useState<Certificate[]>(certificates);
  const [importOpen, setImportOpen] = useState(false);
  const [csv, setCsv] = useState("");
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    setList(certificates);
  }, [certificates]);

  async function refresh() {
    if (USE_MOCK) return;
    setRefreshing(true);
    try {
      const rows = await api.get<Certificate[]>("/certificates");
      setList(rows);
    } catch {
      // оставляем текущий список
    } finally {
      setRefreshing(false);
    }
  }

  async function runImport(dryRun: boolean) {
    if (!csv.trim() || USE_MOCK) return;
    setImporting(true);
    try {
      const result = await api.post<ImportPreview>("/certificates/import", { csv, dryRun });
      setImportPreview(result);
      if (!dryRun) await refresh();
    } finally {
      setImporting(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const hasDateFilter = !!(validFrom || validTo);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return list.filter((c) => {
      if (validFrom && c.validUntil < validFrom) return false;
      if (validTo && c.validUntil > validTo) return false;
      if (status !== "all" && c.status !== status) return false;
      if (type !== "all" && c.type !== type) return false;
      if (!needle) return true;
      const haystack = [
        c.number,
        c.guestName ?? "",
        c.buyerDealNumber != null ? String(c.buyerDealNumber) : "",
        c.type === "plastic" ? "пластик" : "электронный",
        statusLabel[c.status],
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [list, q, validFrom, validTo, status, type]);

  const active = list.filter((c) => c.status === "active");
  const issuedSum = list.reduce((s, c) => s + c.nominal, 0);
  const liability = list.reduce((s, c) => s + c.balance, 0);

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <h1 className="text-2xl font-extrabold text-white mb-1">Реестр сертификатов</h1>
        </div>
        <div className="flex gap-2">
          {user?.role === "admin" && (
            <Button variant="subtle" className="py-2 px-3" onClick={() => setImportOpen(true)}>
              <Upload size={14} /> Импорт CSV
            </Button>
          )}
          <Button variant="subtle" className="py-2 px-3" onClick={() => void refresh()} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            Обновить
          </Button>
        </div>
      </div>
      <Hint>
        Актуальные балансы пластиковых и электронных сертификатов. Поиск и фильтр по сроку.
        «Не погашено» — сумма остатков на балансах. Импорт CSV доступен администратору: сначала
        обязателен прогон без записи.
      </Hint>

      <div className="grid sm:grid-cols-3 gap-3 mb-4 mt-4">
        <StatTile label="Активных" value={String(active.length)} tone="green" />
        <StatTile label="Продано на сумму" value={money(issuedSum)} tone="gold" />
        <StatTile label="Не погашено" value={money(liability)} tone="amber" sub="остатки на балансах" />
      </div>

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input
            className="input pl-9"
            placeholder="Поиск по №, гостю, № заявки…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="min-w-[150px]">
          <div className="text-[11px] uppercase tracking-wider text-mute mb-1">Статус</div>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">Все статусы</option>
            <option value="active">Активные</option>
            <option value="used">Использованные</option>
            <option value="expired">Просроченные</option>
          </select>
        </div>
        <div className="min-w-[150px]">
          <div className="text-[11px] uppercase tracking-wider text-mute mb-1">Тип</div>
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="all">Все типы</option>
            <option value="plastic">Пластик</option>
            <option value="digital">Электронный</option>
          </select>
        </div>
        <div className="min-w-[140px]">
          <div className="text-[11px] uppercase tracking-wider text-mute mb-1">Действует до · с</div>
          <input
            type="date"
            className="input"
            value={validFrom}
            onChange={(e) => setValidFrom(e.target.value)}
          />
        </div>
        <div className="min-w-[140px]">
          <div className="text-[11px] uppercase tracking-wider text-mute mb-1">по</div>
          <input
            type="date"
            className="input"
            value={validTo}
            min={validFrom || undefined}
            onChange={(e) => setValidTo(e.target.value)}
          />
        </div>
        {hasDateFilter && (
          <Button
            variant="subtle"
            className="py-2 px-3"
            onClick={() => {
              setValidFrom("");
              setValidTo("");
            }}
          >
            Сбросить даты
          </Button>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[640px]">
            <thead className="text-[12px] uppercase tracking-wider text-mute border-b border-ink-700">
              <tr>
                <th className="px-4 py-3">№</th>
                <th className="px-4 py-3">Гость</th>
                <th className="px-4 py-3 hidden md:table-cell">Тип</th>
                <th className="px-4 py-3">Номинал</th>
                <th className="px-4 py-3">Остаток</th>
                <th className="px-4 py-3">Действует до</th>
                <th className="px-4 py-3">Статус</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {filtered.map((c) => (
                <tr key={c.number} className="hover:bg-ink-800/40">
                  <td className="px-4 py-3 font-mono text-gold-soft">№{c.number}</td>
                  <td className="px-4 py-3 text-white">{c.guestName || "—"}</td>
                  <td className="px-4 py-3 hidden md:table-cell text-mute text-sm">
                    {c.type === "plastic" ? "Пластик" : "Электронный"}
                  </td>
                  <td className="px-4 py-3 text-mute-soft">{money(c.nominal)}</td>
                  <td className="px-4 py-3">
                    <div className="font-semibold text-emerald-300">{money(c.balance)}</div>
                    <div className="h-1.5 mt-1 w-24 bg-ink-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gold"
                        style={{ width: `${c.nominal > 0 ? (c.balance / c.nominal) * 100 : 0}%` }}
                      />
                    </div>
                  </td>
                  <td className="px-4 py-3 text-mute text-[13px] whitespace-nowrap">{dateRu(c.validUntil)}</td>
                  <td className="px-4 py-3">
                    <Badge tone={statusTone[c.status]}>{statusLabel[c.status]}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    {c.status === "active" && (
                      <Button
                        variant="subtle"
                        className="py-1.5 px-3 text-[13px]"
                        onClick={() => {
                          setRedeem(c);
                          setAmount("");
                        }}
                      >
                        <Wallet size={14} /> Списать
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-mute">
                    <Filter size={20} className="mx-auto mb-2 opacity-50" />
                    {q.trim() || hasDateFilter ? "По фильтру ничего не найдено" : "Сертификатов пока нет"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={!!redeem} onClose={() => setRedeem(null)} title={`Списание с сертификата №${redeem?.number}`}>
        {redeem && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-lg bg-ink-900 border border-ink-700 p-3">
              <Ticket className="text-gold" size={20} />
              <div className="flex-1">
                <div className="text-white font-semibold">Остаток: {money(redeem.balance)}</div>
                <div className="text-mute text-sm">Гость: {redeem.guestName || "—"}</div>
              </div>
            </div>
            <Field label="Сумма списания">
              <input
                className="input"
                inputMode="numeric"
                placeholder="0"
                value={amount}
                onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))}
              />
            </Field>
            {!!amount && Number(amount) > 0 && (
              <div className="text-[13px] text-emerald-300">
                После списания останется: {money(Math.max(0, redeem.balance - Number(amount)))}
              </div>
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setAmount(String(redeem.balance))}>
                Весь баланс
              </Button>
              <div className="flex-1" />
              <Button
                disabled={!amount || Number(amount) <= 0 || Number(amount) > redeem.balance}
                onClick={() => {
                  redeemCertificate(redeem.number, Number(amount));
                  setList((prev) =>
                    prev.map((c) =>
                      c.number === redeem.number
                        ? {
                            ...c,
                            balance: Math.max(0, c.balance - Number(amount)),
                            status:
                              Math.max(0, c.balance - Number(amount)) === 0 ? "used" : c.status,
                          }
                        : c
                    )
                  );
                  setRedeem(null);
                }}
              >
                Списать {amount ? money(Number(amount)) : ""}
              </Button>
            </div>
            <p className="text-[12px] text-mute">
              Если после списания остаток = 0 — статус «Использован». Обычно списание идёт из продажи
              (оплата способом «Сертификат»).
            </p>
          </div>
        )}
      </Modal>
      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="Импорт сертификатов и номиналов">
        <div className="space-y-4">
          <Field label="CSV-файл" hint="Поддерживаются запятая или точка с запятой; сначала обязателен dry-run">
            <input
              type="file"
              accept=".csv,text/csv"
              className="input"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                file.text().then((text) => {
                  setCsv(text);
                  setImportPreview(null);
                });
              }}
            />
          </Field>
          {importPreview && (
            <div className="rounded-lg border border-ink-700 p-3 text-sm text-mute">
              Валидных строк: <strong className="text-white">{importPreview.valid}</strong>
              {" · "}дублей в реестре: <strong className="text-white">{importPreview.conflicts.length}</strong>
              {" · "}ошибок: <strong className={importPreview.errors.length ? "text-amber-300" : "text-white"}>{importPreview.errors.length}</strong>
              {importPreview.imported > 0 && <>{" · "}импортировано: <strong className="text-emerald-300">{importPreview.imported}</strong></>}
              {importPreview.errors.slice(0, 5).map((error) => <div key={`${error.line}:${error.message}`} className="mt-1 text-amber-300">Строка {error.line}: {error.message}</div>)}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="subtle" disabled={!csv || importing} onClick={() => void runImport(true)}>Проверить</Button>
            <Button
              disabled={!importPreview?.dryRun || importPreview.errors.length > 0 || importing}
              onClick={() => void runImport(false)}
            >
              Импортировать
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
