import { useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { api, apiPath, USE_MOCK } from "../api/client";
import type { Deal } from "../data/types";
import { KIND_LABEL } from "../lib/labels";
import { formatPhone, money, shortDate } from "../lib/format";
import { Button, Field } from "./ui";

function phoneTail(value: string): string {
  const digits = value.replace(/\D/g, "");
  return digits.length >= 10 ? digits.slice(-10) : "";
}

function samePhone(deal: Deal, tail: string): boolean {
  return phoneTail(deal.clientPhone) === tail;
}

/** Успех любого вида: продажа, компания, аренда, сертификат, доставка и остальные. */
export function localSuccessByPhone(deals: Deal[], phone: string): Deal[] {
  const tail = phoneTail(phone);
  if (!tail) return [];
  return deals.filter((deal) => deal.stage === "Успех" && samePhone(deal, tail));
}

async function remoteSuccessByPhone(phone: string): Promise<Deal[]> {
  if (USE_MOCK) return [];
  const tail = phoneTail(phone);
  if (!tail) return [];
  const rows = await api.get<Deal[]>(apiPath("/deals/search", { phone: tail }));
  return rows.filter((deal) => deal.stage === "Успех" && (!phoneTail(deal.clientPhone) || samePhone(deal, tail)));
}

export function SourceDealSearch({
  deals,
  source,
  unlinked,
  clientName,
  onClientName,
  onPhone,
  onPick,
  onUnlinked,
  onClear,
}: {
  deals: Deal[];
  source: Deal | null;
  unlinked: boolean;
  clientName: string;
  onClientName: (name: string) => void;
  onPhone: (phone: string) => void;
  onPick: (deal: Deal) => void;
  onUnlinked: (phone: string, name: string) => void;
  onClear: () => void;
}) {
  const [phone, setPhone] = useState(source?.clientPhone ?? "");
  const [matches, setMatches] = useState<Deal[]>([]);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function openSourceDeal(number: number) {
    window.location.hash = `board/all/all/${number}`;
  }

  async function find() {
    const tail = phoneTail(phone);
    if (!tail) {
      setError("Введите телефон целиком");
      setSearched(true);
      setMatches([]);
      return;
    }
    setBusy(true);
    setError(null);
    setSearched(true);
    try {
      const local = localSuccessByPhone(deals, phone);
      const remote = await remoteSuccessByPhone(phone).catch(() => [] as Deal[]);
      const coveredAmo = new Set(local.flatMap((deal) => (deal.amoLeadId ? [deal.amoLeadId] : [])));
      const byNumber = new Map<number, Deal>();
      for (const deal of remote) {
        if (coveredAmo.has(deal.number)) continue;
        byNumber.set(deal.number, deal);
      }
      for (const deal of local) byNumber.set(deal.number, deal);
      const found = [...byNumber.values()].sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      setMatches(found);
      if (found.length === 1) onPick(found[0]);
    } finally {
      setBusy(false);
    }
  }

  if (source) {
    return (
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <div className="text-[13px] text-white min-w-0">
          <span className="font-semibold">#{source.number}</span>
          <span className="text-mute">
            {" "}
            · {KIND_LABEL[source.kind] ?? source.kind} · {source.clientName} · {formatPhone(source.clientPhone)} ·{" "}
            {money(source.total)}
          </span>
        </div>
        <div className="flex gap-2">
          <Button variant="subtle" onClick={() => openSourceDeal(source.number)}>
            <ExternalLink size={14} /> Открыть
          </Button>
          <Button variant="subtle" onClick={onClear}>
            Другая заявка
          </Button>
        </div>
      </div>
    );
  }

  if (unlinked) {
    return (
      <div className="space-y-3">
        <div className="text-[13px] text-mute">Без привязки к заявке. Телефон и имя клиента нужны для чека.</div>
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Телефон клиента" required>
            <input
              className="input"
              inputMode="tel"
              value={phone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => {
                const next = formatPhone(e.target.value);
                setPhone(next);
                onPhone(next);
              }}
            />
          </Field>
          <Field label="Имя клиента" required>
            <input className="input" value={clientName} onChange={(e) => onClientName(e.target.value)} />
          </Field>
        </div>
        <Button variant="subtle" onClick={onClear}>
          Вернуться к поиску
        </Button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <Field label="Телефон клиента">
            <input
              className="input"
              inputMode="tel"
              value={phone}
              placeholder="+7 (___) ___-__-__"
              onChange={(e) => {
                setPhone(formatPhone(e.target.value));
                setSearched(false);
                setMatches([]);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") void find();
              }}
            />
          </Field>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void find()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-white font-semibold px-3 py-2.5 disabled:opacity-50"
        >
          <Search size={16} /> {busy ? "Ищем…" : "Найти"}
        </button>
      </div>
      {error && <div className="mt-3 text-[12px] text-amber-300/80">{error}</div>}
      {searched && !busy && matches.length === 0 && !error && (
        <div className="mt-3 text-[12px] text-amber-300/80">
          Успешных заявок с этим телефоном нет. Можно провести без привязки.
        </div>
      )}
      {matches.length > 1 && (
        <div className="mt-3 space-y-1.5">
          {matches.map((deal) => (
            <button
              key={deal.id || deal.number}
              type="button"
              onClick={() => onPick(deal)}
              className="w-full text-left rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 hover:border-ink-500"
            >
              <span className="text-white text-[13px] font-semibold">#{deal.number}</span>
              <span className="text-mute text-[13px]">
                {" "}
                · {KIND_LABEL[deal.kind] ?? deal.kind} · {deal.clientName} · {money(deal.total)} ·{" "}
                {shortDate(deal.createdAt)}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="mt-3">
        <Button variant="subtle" onClick={() => onUnlinked(formatPhone(phone), clientName.trim())}>
          Провести без привязки к заявке
        </Button>
      </div>
    </div>
  );
}
