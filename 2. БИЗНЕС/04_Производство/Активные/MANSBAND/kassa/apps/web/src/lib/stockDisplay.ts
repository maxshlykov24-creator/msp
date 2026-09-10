import { ITEM_LOCATIONS } from "@kassa/shared";

export type LocSlot = { label: string; match: (name: string) => boolean };

export const MAIN_WAREHOUSES: { id: string; label: string; match: (name: string) => boolean }[] = [
  {
    id: "novo",
    label: "Новокузнецкая",
    match: (n) => /новокузнецк/i.test(n) && !/полупарк/i.test(n),
  },
  {
    id: "bauman",
    label: "Бауманская",
    match: (n) => /бауманск/i.test(n) && !/полупарк/i.test(n),
  },
  {
    id: "central",
    label: "Центральный",
    match: (n) => /^центральн/i.test(n.trim()) || /центральный склад/i.test(n),
  },
];

export const EXPANDED_COLUMNS: { id: string; slots: LocSlot[] }[] = [
  {
    id: "novo",
    slots: [
      { label: "Новокузнецкая", match: (n) => /новокузнецк/i.test(n) && !/полупарк/i.test(n) },
      { label: "Полупарки на Новокузнецкой", match: (n) => /полупарк/i.test(n) && /новокузнецк/i.test(n) },
      { label: "Ателье", match: (n) => /ателье/i.test(n) },
    ],
  },
  {
    id: "bauman",
    slots: [
      { label: "Бауманская", match: (n) => /бауманск/i.test(n) && !/полупарк/i.test(n) },
      { label: "Полупарки на Бауманской", match: (n) => /полупарк/i.test(n) && /бауманск/i.test(n) },
    ],
  },
  {
    id: "central",
    slots: [
      { label: "Центральный", match: (n) => /^центральн/i.test(n.trim()) || /центральный склад/i.test(n) },
      { label: "В пути", match: (n) => /в пути/i.test(n) },
      { label: "СДЭК", match: (n) => /^сдэк/i.test(n.trim()) },
    ],
  },
];

export function fmtQty(n: number): string {
  if (!Number.isFinite(n)) return "0";
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\.?0+$/, "");
}

export function qtyClass(n: number): string {
  if (n < 0) return "text-amber-300 font-semibold";
  if (n > 0) return "text-emerald-300 font-semibold";
  return "text-mute";
}

export function qtyAt(rows: Array<{ name: string; available?: number; whole?: number }>, match: (name: string) => boolean): number {
  const row = rows.find((w) => match(w.name));
  if (!row) return 0;
  return row.available ?? row.whole ?? 0;
}

function slotMatched(name: string): boolean {
  return EXPANDED_COLUMNS.some((col) => col.slots.some((s) => s.match(name)));
}

export function leftoverStock<T extends { name: string }>(rows: T[]): T[] {
  return rows
    .filter((w) => !slotMatched(w.name))
    .sort((a, b) => {
      const ai = ITEM_LOCATIONS.findIndex((loc) => loc.toLowerCase() === a.name.trim().toLowerCase());
      const bi = ITEM_LOCATIONS.findIndex((loc) => loc.toLowerCase() === b.name.trim().toLowerCase());
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || a.name.localeCompare(b.name, "ru");
    });
}
