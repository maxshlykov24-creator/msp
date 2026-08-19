import { Send, Check, ArrowLeftRight, Banknote, Clock3 } from "lucide-react";
import { useStore } from "../store";
import { money, timeOf, shortDate } from "../lib/format";
import { StatTile } from "../components/ui";

export function EdwinQueue() {
  const { queue, issueQueueItem } = useStore();
  const pending = queue.filter((q) => q.status === "pending");
  const issued = queue.filter((q) => q.status === "issued");
  const pendingSum = pending.reduce((s, q) => s + q.amount, 0);

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Send className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Очередь Эдвина</h1>
      </div>
      <p className="text-mute text-sm mb-5">
        Сдача и возвраты со статусом «не выдано». Эдвин переводит и смахивает — всё «не выдано» к утру авто-перейдёт в «выдано».
      </p>

      <div className="grid grid-cols-2 gap-3 mb-6">
        <StatTile label="К выдаче сейчас" value={String(pending.length)} tone="amber" sub={money(pendingSum)} />
        <StatTile label="Выдано сегодня" value={String(issued.length)} tone="green" />
      </div>

      <div className="field-label mb-2">Не выдано</div>
      <div className="space-y-2.5 mb-7">
        {pending.length === 0 && (
          <div className="card text-center text-mute py-8">Очередь пуста — всё выдано</div>
        )}
        {pending.map((q) => (
          <div key={q.id} className="card p-0 overflow-hidden flex">
            <div className={`w-1.5 ${q.kind === "refund" ? "bg-white/70" : "bg-white/35"}`} />
            <div className="flex-1 p-4 flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl grid place-items-center bg-white/10 text-white">
                {q.kind === "refund" ? <ArrowLeftRight size={18} /> : <Banknote size={18} />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-white font-semibold">{money(q.amount)}</span>
                  <span className="chip bg-ink-700 text-mute">{q.kind === "refund" ? "Возврат" : "Сдача"}</span>
                  <span className="text-mute text-[12px]">#{q.dealNumber}</span>
                </div>
                <div className="text-mute text-[13px] truncate">{q.client} · {q.destination}</div>
              </div>
              <button
                onClick={() => issueQueueItem(q.id)}
                className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-3 py-2 hover:bg-white/90 active:scale-95 transition"
              >
                <Check size={16} /> Отправил
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="field-label mb-2 flex items-center gap-1.5"><Clock3 size={13} /> Выдано</div>
      <div className="space-y-2">
        {issued.map((q) => (
          <div key={q.id} className="flex items-center gap-3 px-4 py-3 rounded-lg bg-ink-900/50 border border-ink-800 opacity-75">
            <Check size={16} className="text-white/70" />
            <span className="text-mute-soft text-sm flex-1">{q.client} · {money(q.amount)} · {q.kind === "refund" ? "возврат" : "сдача"}</span>
            <span className="text-mute text-[12px]">{shortDate(q.createdAt)} {timeOf(q.createdAt)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
