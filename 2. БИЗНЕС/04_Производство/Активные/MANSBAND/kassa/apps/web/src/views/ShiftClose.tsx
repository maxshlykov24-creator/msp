import { useState } from "react";
import { Wallet, CheckCircle2, AlertTriangle } from "lucide-react";
import { Card, Button } from "../components/ui";
import { money } from "../lib/format";

const EXPECTED = [
  { method: "Наличные", system: 41800 },
  { method: "Сбер Женя", system: 73600 },
  { method: "Альфа Эдвин", system: 28900 },
  { method: "РС", system: 30000 },
];

export function ShiftClose() {
  const [fact, setFact] = useState<Record<string, string>>({});
  const [closed, setClosed] = useState(false);

  const rows = EXPECTED.map((e) => {
    const f = Number(fact[e.method] ?? "");
    const diff = (fact[e.method] === undefined || fact[e.method] === "") ? null : f - e.system;
    return { ...e, fact: f, diff };
  });
  const allFilled = rows.every((r) => r.diff !== null);
  const totalDiff = rows.reduce((s, r) => s + (r.diff ?? 0), 0);

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Wallet className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Закрытие смены</h1>
      </div>
      <p className="text-mute text-sm mb-5">Сверка факта с системой по способам оплаты. Без сверки смена не закрывается.</p>

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-left">
          <thead className="text-[12px] uppercase tracking-wider text-mute border-b border-ink-700">
            <tr>
              <th className="px-4 py-3">Способ</th>
              <th className="px-4 py-3">В системе</th>
              <th className="px-4 py-3">Факт</th>
              <th className="px-4 py-3">Расхождение</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {rows.map((r) => (
              <tr key={r.method}>
                <td className="px-4 py-3 text-white">{r.method}</td>
                <td className="px-4 py-3 text-mute-soft">{money(r.system)}</td>
                <td className="px-4 py-3">
                  <input
                    className="input py-1.5 w-32"
                    inputMode="numeric"
                    placeholder="0"
                    value={fact[r.method] ?? ""}
                    onChange={(e) => setFact({ ...fact, [r.method]: e.target.value.replace(/[^\d]/g, "") })}
                  />
                </td>
                <td className="px-4 py-3 font-semibold">
                  {r.diff === null ? <span className="text-mute">—</span> :
                    r.diff === 0 ? <span className="text-white/60">точно</span> :
                    <span className="text-white underline decoration-white/30 underline-offset-4">{r.diff > 0 ? "+" : ""}{money(r.diff)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="mt-5">
        {closed ? (
          <div className="flex items-center gap-2 rounded-xl bg-white/10 border border-white/30 text-white px-4 py-3 font-semibold">
            <CheckCircle2 size={18} /> Смена закрыта. Можно выходить из аккаунта.
          </div>
        ) : (
          <div className="flex items-center gap-3">
            {allFilled && totalDiff !== 0 && (
              <span className="inline-flex items-center gap-1.5 text-mute-soft text-sm">
                <AlertTriangle size={15} /> Общее расхождение {money(totalDiff)} — проверьте кассу
              </span>
            )}
            <div className="flex-1" />
            <Button disabled={!allFilled} onClick={() => setClosed(true)}>Закрыть смену</Button>
          </div>
        )}
      </div>
    </div>
  );
}
