import { BarChart3, Store, Headset } from "lucide-react";
import { StatTile, Card } from "../components/ui";
import { moneyPlain } from "../lib/format";

const CONSULTANTS = [
  { name: "Матвей", deals: 48, conv: 71, revenue: 1290000 },
  { name: "Женя", deals: 41, conv: 64, revenue: 980000 },
  { name: "Гриша", deals: 37, conv: 59, revenue: 870000 },
  { name: "Саша", deals: 29, conv: 52, revenue: 610000 },
  { name: "Арсен", deals: 22, conv: 47, revenue: 470000 },
];

function Bar({ value, max, tone = "bg-gold" }: { value: number; max: number; tone?: string }) {
  return (
    <div className="h-2 bg-ink-700 rounded-full overflow-hidden">
      <div className={`h-full ${tone} rounded-full`} style={{ width: `${(value / max) * 100}%` }} />
    </div>
  );
}

export function Dashboard() {
  const maxRev = Math.max(...CONSULTANTS.map((c) => c.revenue));
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Статистика</h1>
      </div>
      <p className="text-mute text-sm mb-5">Отдел продаж · магазин и call-менеджер раздельно</p>

      <div className="grid sm:grid-cols-4 gap-3 mb-6">
        <StatTile label="Выручка за месяц, ₽" value={moneyPlain(4220000)} tone="gold" />
        <StatTile label="Заявок" value="177" tone="gray" sub="+12% к прошлому" />
        <StatTile label="Конверсия" value="61%" tone="green" />
        <StatTile label="Средний чек, ₽" value={moneyPlain(31400)} tone="blue" />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="text-white font-bold mb-4">Консультанты — выручка, ₽ и конверсия</h3>
          <div className="space-y-4">
            {CONSULTANTS.map((c) => (
              <div key={c.name}>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-white font-medium">{c.name}</span>
                  <span className="text-mute">{moneyPlain(c.revenue)} · <span className="text-white">{c.conv}%</span></span>
                </div>
                <Bar value={c.revenue} max={maxRev} />
              </div>
            ))}
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <div className="flex items-center gap-2 mb-4">
              <Store size={18} className="text-gold" />
              <h3 className="text-white font-bold">По магазинам, ₽</h3>
            </div>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-sm mb-1.5"><span className="text-white">На Бауманской</span><span className="text-mute">{moneyPlain(2680000)}</span></div>
                <Bar value={2680000} max={2680000} />
              </div>
              <div>
                <div className="flex justify-between text-sm mb-1.5"><span className="text-white">На Пятницкой</span><span className="text-mute">{moneyPlain(1540000)}</span></div>
                <Bar value={1540000} max={2680000} tone="bg-gold-dim" />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center gap-2 mb-4">
              <Headset size={18} className="text-gold" />
              <h3 className="text-white font-bold">Магазин vs Call-менеджер</h3>
            </div>
            <div className="grid grid-cols-2 gap-3 text-center">
              <div className="rounded-lg bg-ink-900 border border-ink-700 p-4">
                <div className="text-2xl font-extrabold text-white">68%</div>
                <div className="text-mute text-[12px] mt-1">продаж в магазине</div>
              </div>
              <div className="rounded-lg bg-ink-900 border border-ink-700 p-4">
                <div className="text-2xl font-extrabold text-gold-soft">32%</div>
                <div className="text-mute text-[12px] mt-1">через call-менеджера</div>
              </div>
            </div>
            <p className="text-[12px] text-mute mt-3">Заявки без нашего онлайн-участия в статистику не попадают.</p>
          </Card>
        </div>
      </div>
    </div>
  );
}
