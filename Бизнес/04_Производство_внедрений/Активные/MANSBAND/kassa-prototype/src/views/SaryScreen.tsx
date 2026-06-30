import { useState } from "react";
import { Gift, Check, Camera, Copy, Clock3 } from "lucide-react";
import { useStore } from "../store";
import { money, timeOf, shortDate } from "../lib/format";
import { StatTile } from "../components/ui";
import type { SaryPayout } from "../data/types";

/** Авто-текст сообщения клиенту о выплате «сары». */
function saryMessage(s: SaryPayout): string {
  return `Здравствуйте, ${s.client}! Спасибо за рекомендацию — отправляем вам сары ${money(s.amount)}. С уважением, MANSBAND.`;
}

export function SaryScreen() {
  const { sary, markSarySent } = useStore();
  const pending = sary.filter((s) => s.status === "pending");
  const sent = sary.filter((s) => s.status === "sent");
  const pendingSum = pending.reduce((s, x) => s + x.amount, 0);
  const [copied, setCopied] = useState<string | null>(null);

  function copyMsg(s: SaryPayout) {
    navigator.clipboard?.writeText(saryMessage(s)).catch(() => {});
    setCopied(s.id);
    setTimeout(() => setCopied((c) => (c === s.id ? null : c)), 1500);
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Gift className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Сары</h1>
      </div>
      <p className="text-mute text-sm mb-5">
        Реферальные выплаты клиентам за совет. Отправили перевод — приложите скриншот сообщения и отметьте «Отправлено».
      </p>

      <div className="grid grid-cols-2 gap-3 mb-6">
        <StatTile label="К отправке" value={String(pending.length)} tone="amber" sub={money(pendingSum)} />
        <StatTile label="Отправлено" value={String(sent.length)} tone="green" />
      </div>

      <div className="field-label mb-2">Не отправлено</div>
      <div className="space-y-2.5 mb-7">
        {pending.length === 0 && (
          <div className="card text-center text-mute py-8">Все выплаты отправлены</div>
        )}
        {pending.map((s) => (
          <div key={s.id} className="card p-4 space-y-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl grid place-items-center bg-white/10 text-white">
                <Gift size={18} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-white font-semibold">{money(s.amount)}</span>
                  <span className="text-mute text-[12px]">{s.client} · {s.phone}</span>
                </div>
                <div className="text-mute text-[13px] truncate">{s.reason}</div>
              </div>
              <button
                onClick={() => markSarySent(s.id)}
                className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-3 py-2 hover:bg-white/90 active:scale-95 transition"
              >
                <Check size={16} /> Отправлено
              </button>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => copyMsg(s)}
                className="chip bg-ink-700 text-mute-soft hover:text-white inline-flex items-center gap-1.5"
              >
                <Copy size={13} /> {copied === s.id ? "Скопировано" : "Текст сообщения"}
              </button>
              <span className="chip bg-ink-800 text-mute inline-flex items-center gap-1.5">
                <Camera size={13} /> Скриншот при отправке
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="field-label mb-2 flex items-center gap-1.5"><Clock3 size={13} /> Отправлено</div>
      <div className="space-y-2">
        {sent.map((s) => (
          <div key={s.id} className="flex items-center gap-3 px-4 py-3 rounded-lg bg-ink-900/50 border border-ink-800 opacity-75">
            <Check size={16} className="text-white/70" />
            <span className="text-mute-soft text-sm flex-1">{s.client} · {money(s.amount)}{s.screenshotAttached ? " · скрин" : ""}</span>
            <span className="text-mute text-[12px]">{shortDate(s.createdAt)} {timeOf(s.createdAt)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
