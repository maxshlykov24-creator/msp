import { suitPartOf } from "./constants.js";
import type { SuitPart } from "./types.js";

/**
 * Разделы зала для поиска товара. Это не дерево МойСклада: там костюм лежит
 * пиджаком, брюками и жилетом в одной папке, а сорочки ошибочно тоже в
 * «1. Костюмы». Консультант ищет по виду вещи — двойка, брюки отдельно,
 * пальто, галстук.
 */

export type HallSectionId = "suits" | "clothes" | "outerwear" | "shoes" | "accessories";

export interface HallGroupDef {
  id: string;
  label: string;
  /** Подстроки вида (имя до скобки). Длинные матчи побеждают короткие. */
  nameIncludes: string[];
}

export interface HallSectionDef {
  id: HallSectionId;
  label: string;
  groups: HallGroupDef[];
}

export const HALL_SECTIONS: HallSectionDef[] = [
  {
    id: "suits",
    label: "Костюмы",
    groups: [
      { id: "double", label: "Двойки", nameIncludes: [] },
      { id: "triple", label: "Тройки", nameIncludes: [] },
      { id: "smoking", label: "Смокинги", nameIncludes: [] },
    ],
  },
  {
    id: "clothes",
    label: "Одежда",
    groups: [
      { id: "trousers", label: "Брюки", nameIncludes: ["палаццо", "слакс", "чинос", "джинс", "брюк"] },
      { id: "shirts", label: "Рубашки", nameIncludes: ["сорочк", "рубашк"] },
      { id: "polo", label: "Поло", nameIncludes: ["поло"] },
      { id: "knit", label: "Трикотаж", nameIncludes: ["водолазк", "кардиган", "свитер", "джемпер", "трикотаж"] },
      { id: "tees", label: "Футболки", nameIncludes: ["футболк", "майк"] },
      { id: "other", label: "Прочее", nameIncludes: [] },
    ],
  },
  {
    id: "outerwear",
    label: "Верхняя одежда",
    groups: [
      { id: "quilted", label: "Стёганое", nameIncludes: ["стеганн"] },
      { id: "coat", label: "Пальто", nameIncludes: ["пальто"] },
      { id: "trench", label: "Тренчи", nameIncludes: ["тренч"] },
      { id: "bomber", label: "Бомберы", nameIncludes: ["бомбер"] },
      { id: "safari", label: "Сафари", nameIncludes: ["сафари"] },
      { id: "other", label: "Прочее", nameIncludes: [] },
    ],
  },
  {
    id: "shoes",
    label: "Обувь",
    groups: [
      { id: "derby", label: "Дерби", nameIncludes: ["дерби"] },
      { id: "oxford", label: "Оксфорды", nameIncludes: ["оксфорд"] },
      { id: "monk", label: "Монки", nameIncludes: ["монки"] },
      { id: "loafer", label: "Лоферы", nameIncludes: ["лофер"] },
      { id: "sandal", label: "Сандалии", nameIncludes: ["сандал"] },
      { id: "espadrille", label: "Эспадрильи", nameIncludes: ["эспадриль"] },
      { id: "other", label: "Прочее", nameIncludes: [] },
    ],
  },
  {
    id: "accessories",
    label: "Аксессуары",
    groups: [
      { id: "ties", label: "Галстуки", nameIncludes: ["галстук"] },
      { id: "bows", label: "Бабочки", nameIncludes: ["бабочк"] },
      { id: "caps", label: "Головные уборы", nameIncludes: ["восьмиклин", "кепк", "шляп", "берет"] },
      { id: "socks", label: "Носки", nameIncludes: ["носк"] },
      { id: "underwear", label: "Бельё", nameIncludes: ["боксер"] },
      { id: "belts", label: "Ремни", nameIncludes: ["ремен"] },
      { id: "suspenders", label: "Подтяжки", nameIncludes: ["подтяжк"] },
      { id: "scarves", label: "Шарфы и платки", nameIncludes: ["шарф", "платок", "платк"] },
      { id: "cufflinks", label: "Запонки", nameIncludes: ["запонк"] },
      { id: "cummerbund", label: "Камербанд", nameIncludes: ["камербанд"] },
      { id: "other", label: "Прочее", nameIncludes: [] },
    ],
  },
];

export const HALL_SECTION_IDS = HALL_SECTIONS.map((s) => s.id);

export function hallSectionOf(id: HallSectionId): HallSectionDef {
  return HALL_SECTIONS.find((s) => s.id === id) ?? HALL_SECTIONS[0]!;
}

export function hallSectionLabel(id: string): string {
  return HALL_SECTIONS.find((s) => s.id === id)?.label ?? id;
}

/** Вид без характеристик: «Брюки палаццо (48, чёрный)» → «Брюки палаццо». */
export function productKindName(name: string | undefined | null): string {
  return (name ?? "").split(" (")[0]!.trim();
}

export function normalizeHallSection(raw: string | undefined | null): HallSectionId | null {
  const key = (raw ?? "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/^\d+\.\s*/, "")
    .trim();
  if (!key) return null;
  if (key === "suits" || key.includes("костюм")) return "suits";
  if (key === "outerwear" || key.includes("верхн")) return "outerwear";
  if (key === "clothes" || key.includes("одежд")) return "clothes";
  if (key === "shoes" || key.includes("обув")) return "shoes";
  if (key === "accessories" || key.includes("аксессуар")) return "accessories";
  return null;
}

const SKIP_KIND = /сертификат|^товар$/i;

export function isHallHidden(name: string | undefined | null): boolean {
  return SKIP_KIND.test(productKindName(name));
}

const STANDALONE_TROUSERS = /палаццо|слакс|чинос|джинс/i;

/** Штучные брюки зала: слаксы, палаццо, чинос. Не парные брюки костюма. */
export function isStandaloneTrousersName(name: string | undefined | null): boolean {
  return STANDALONE_TROUSERS.test(productKindName(name));
}

function clothesFolderNotSuit(category: string | undefined | null): boolean {
  const cat = (category ?? "").toLowerCase();
  return /одежд/.test(cat) && !/костюм/.test(cat) && !/верхн/.test(cat);
}

/**
 * Часть костюма, её в штучных разделах нет: пиджак и жилет всегда,
 * брюки костюма тоже. Слаксы и палаццо, а также брюки из одежды — штучные.
 */
export function isSuitPieceForHall(input: {
  name?: string | null;
  category?: string | null;
  suitPart?: SuitPart | null;
}): boolean {
  const part = input.suitPart ?? suitPartOf(input.name);
  if (!part) return false;
  if (part === "jacket" || part === "vest") return true;
  if (part !== "trousers") return false;
  if (isStandaloneTrousersName(input.name)) return false;
  if (clothesFolderNotSuit(input.category)) return false;
  return true;
}

export interface HallClass {
  section: HallSectionId;
  group: string;
}

function matchNamedGroup(kind: string): HallClass | null {
  const n = kind.toLowerCase().replace(/ё/g, "е");
  let best: { hit: HallClass; weight: number } | null = null;
  for (const section of HALL_SECTIONS) {
    if (section.id === "suits") continue;
    for (const group of section.groups) {
      for (const needle of group.nameIncludes) {
        if (!n.includes(needle)) continue;
        if (!best || needle.length > best.weight) {
          best = { hit: { section: section.id, group: group.id }, weight: needle.length };
        }
      }
    }
  }
  return best?.hit ?? null;
}

function fallbackSection(category: string | undefined | null): HallSectionId | null {
  return normalizeHallSection(category?.split(/[/\\]/)[0] ?? "");
}

/** Куда положить штучную позицию в зале. Костюмные части → suits, их в штучный список не кладём. */
export function classifyHall(input: {
  name?: string | null;
  category?: string | null;
  suitPart?: SuitPart | null;
}): HallClass | null {
  if (isHallHidden(input.name)) return null;
  if (isSuitPieceForHall(input)) return { section: "suits", group: "parts" };
  const kind = productKindName(input.name);
  const named = matchNamedGroup(kind);
  if (named) return named;
  const section = fallbackSection(input.category);
  if (!section || section === "suits") return null;
  return { section, group: "other" };
}

export function hallGroupsOf(section: HallSectionId): HallGroupDef[] {
  return hallSectionOf(section).groups.filter((g) => g.nameIncludes.length > 0 || g.id === "other");
}

export function hallSectionKeywords(section: HallSectionId): string[] {
  return hallSectionOf(section).groups.flatMap((g) => g.nameIncludes);
}

export function hallNamedKeywords(exceptSection?: HallSectionId): string[] {
  return HALL_SECTIONS.filter((s) => s.id !== "suits" && s.id !== exceptSection).flatMap((s) =>
    s.groups.flatMap((g) => g.nameIncludes)
  );
}
