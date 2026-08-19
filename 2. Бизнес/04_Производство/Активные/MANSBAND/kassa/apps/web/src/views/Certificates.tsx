import { useState, useMemo } from "react";
import { Ticket, Wallet, Search, Filter } from "lucide-react";
import { useStore } from "../store";
import { money, dateRu } from "../lib/format";
import { Badge, Button, Modal, Field, StatTile } from "../components/ui";
import type { Certificate } from "../data/types";

const statusTone = { active: "green", used: "gray", expired: "red" } as const;
const statusLabel = { active: "Активен", used: "Использован", expired: "Просрочен" } as const;

export function Certificates() {
  const { certificates, redeemCertificate } = useStore();
  const [redeem, setRedeem] = useState<Certificate | null>(null);
  const [amount, setAmount] = useState("");
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return certificates;
    return certificates.filter((c) => {
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
  }, [certificates, q]);

  const active = certificates.filter((c) => c.status === "active");
  const issuedSum = certificates.reduce((s, c) => s + c.nominal, 0);
  const liability = certificates.reduce((s, c) => s + c.balance, 0);

  return (
    <div>
      <h1 className="text-2xl font-extrabold text-white mb-1">Сертификаты</h1>
      <p className="text-mute text-sm mb-5">Реестр с балансом · в «Успех» уходят при использовании</p>

      <div className="grid sm:grid-cols-3 gap-3 mb-4">
        <StatTile label="Активных" value={String(active.length)} tone="green" />
        <StatTile label="Продано на сумму" value={money(issuedSum)} tone="gold" />
        <StatTile label="Не погашено (обязательство)" value={money(liability)} tone="amber" sub="остатки на балансах" />
      </div>

      <div className="relative mb-4 max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
        <input
          className="input pl-9"
          placeholder="Поиск по №, гостю, № заявки…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-left">
          <thead className="text-[12px] uppercase tracking-wider text-mute border-b border-ink-700">
            <tr>
              <th className="px-4 py-3">№</th>
              <th className="px-4 py-3">Гость</th>
              <th className="px-4 py-3 hidden md:table-cell">Тип</th>
              <th className="px-4 py-3">Номинал</th>
              <th className="px-4 py-3">Баланс</th>
              <th className="px-4 py-3 hidden lg:table-cell">Действует до</th>
              <th className="px-4 py-3">Статус</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {filtered.map((c) => (
              <tr key={c.number} className="hover:bg-ink-800/40">
                <td className="px-4 py-3 font-mono text-gold-soft">№{c.number}</td>
                <td className="px-4 py-3 text-white">{c.guestName}</td>
                <td className="px-4 py-3 hidden md:table-cell text-mute text-sm">{c.type === "plastic" ? "Пластик" : "Электронный"}</td>
                <td className="px-4 py-3 text-mute-soft">{money(c.nominal)}</td>
                <td className="px-4 py-3">
                  <div className="font-semibold text-white">{money(c.balance)}</div>
                  <div className="h-1.5 mt-1 w-24 bg-ink-700 rounded-full overflow-hidden">
                    <div className="h-full bg-gold" style={{ width: `${(c.balance / c.nominal) * 100}%` }} />
                  </div>
                </td>
                <td className="px-4 py-3 hidden lg:table-cell text-mute text-[13px]">{dateRu(c.validUntil)}</td>
                <td className="px-4 py-3"><Badge tone={statusTone[c.status]}>{statusLabel[c.status]}</Badge></td>
                <td className="px-4 py-3">
                  {c.status === "active" && (
                    <Button variant="subtle" className="py-1.5 px-3 text-[13px]" onClick={() => { setRedeem(c); setAmount(""); }}>
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
                  {q.trim() ? "По запросу ничего не найдено" : "Сертификатов пока нет"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal open={!!redeem} onClose={() => setRedeem(null)} title={`Списание с сертификата №${redeem?.number}`}>
        {redeem && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-lg bg-ink-900 border border-ink-700 p-3">
              <Ticket className="text-gold" size={20} />
              <div className="flex-1">
                <div className="text-white font-semibold">Баланс: {money(redeem.balance)}</div>
                <div className="text-mute text-sm">Гость: {redeem.guestName}</div>
              </div>
            </div>
            <Field label="Сумма списания">
              <input className="input" inputMode="numeric" placeholder="0" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setAmount(String(redeem.balance))}>Весь баланс</Button>
              <div className="flex-1" />
              <Button
                disabled={!amount || Number(amount) <= 0}
                onClick={() => {
                  redeemCertificate(redeem.number, Number(amount));
                  setRedeem(null);
                }}
              >
                Списать {amount ? money(Number(amount)) : ""}
              </Button>
            </div>
            <p className="text-[12px] text-mute">
              Если после списания баланс = 0 — сертификат уходит в «Использован», а заявка-продажа сертификата — в «Успех».
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
