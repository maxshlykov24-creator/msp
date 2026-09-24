import { StatTile } from "./ui";

export type QueuePeriod = "day" | "week" | "month" | "all";

const PERIODS: Array<{ id: QueuePeriod; label: string }> = [
  { id: "day", label: "Сегодня" },
  { id: "week", label: "7 дней" },
  { id: "month", label: "30 дней" },
  { id: "all", label: "Всё" },
];

function localYmd(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${m}-${day}`;
}

export function periodStart(period: QueuePeriod): string | null {
  if (period === "all") return null;
  const date = new Date();
  if (period === "week") date.setDate(date.getDate() - 6);
  if (period === "month") date.setDate(date.getDate() - 29);
  return localYmd(date);
}

export function inPeriod(iso: string | undefined, from: string | null): boolean {
  if (!from) return true;
  if (!iso) return false;
  const date = new Date(iso);
  const day = Number.isNaN(date.getTime()) ? iso.slice(0, 10) : localYmd(date);
  return day >= from;
}

export function QueuePeriodBar({
  period,
  onChange,
  tiles,
}: {
  period: QueuePeriod;
  onChange: (next: QueuePeriod) => void;
  tiles: Array<{ label: string; value: string; sub?: string; tone?: "gold" | "green" | "amber" | "blue" | "red" | "gray" }>;
}) {
  return (
    <div className="mb-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {PERIODS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            className={`chip transition ${
              period === item.id ? "bg-gold text-ink-950 font-semibold" : "bg-ink-700 text-mute-soft hover:text-white"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {tiles.map((tile) => (
          <StatTile key={tile.label} label={tile.label} value={tile.value} sub={tile.sub} tone={tile.tone ?? "gray"} />
        ))}
      </div>
    </div>
  );
}
