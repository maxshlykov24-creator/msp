import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Filter,
  Loader2,
  Printer,
  ScanLine,
  Search,
  X,
} from "lucide-react";
import { ITEM_LOCATIONS } from "@kassa/shared";
import type { Product, WarehouseStockLine } from "../data/types";
import { api, apiBlob, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { money } from "../lib/format";
import { BarcodeScannerModal } from "../components/BarcodeScanner";

type LocSlot = { label: string; match: (name: string) => boolean };

/** Порядок разделов зала — не дерево МойСклад. */
const SECTION_ORDER = [
  "1. Костюмы",
  "2. Одежда",
  "3. Верхняя одежда",
  "4. Обувь",
  "5. Аксессуары",
] as const;

const MAIN_WAREHOUSES: { id: string; label: string; match: (name: string) => boolean }[] = [
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

const EXPANDED_COLUMNS: { id: string; slots: LocSlot[] }[] = [
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
    ],
  },
];

interface ParsedProduct {
  product: Product;
  section: string;
  /** Первый уровень под разделом зала (для чипов и дерева). */
  subSection: string;
  /** Полный хвост path после раздела (для подписи в строке). */
  subCategory: string;
  baseName: string;
  color: string;
  size: string;
  variation: string;
  modsLabel: string;
}

const EMPTY_SUBSECTION = "Без подраздела";

function fmtQty(n: number): string {
  if (!Number.isFinite(n)) return "0";
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\.?0+$/, "");
}

function qtyClass(n: number): string {
  if (n < 0) return "text-amber-300 font-semibold";
  if (n > 0) return "text-emerald-300 font-semibold";
  return "text-mute";
}

function qtyAt(warehouses: WarehouseStockLine[], match: (name: string) => boolean): number {
  const row = warehouses.find((w) => match(w.name));
  return row?.available ?? 0;
}

function slotMatched(name: string): boolean {
  return EXPANDED_COLUMNS.some((col) => col.slots.some((s) => s.match(name)));
}

function leftoverWarehouses(warehouses: WarehouseStockLine[]): WarehouseStockLine[] {
  return warehouses
    .filter((w) => !slotMatched(w.name))
    .sort((a, b) => {
      const ai = ITEM_LOCATIONS.findIndex((loc) => loc.toLowerCase() === a.name.trim().toLowerCase());
      const bi = ITEM_LOCATIONS.findIndex((loc) => loc.toLowerCase() === b.name.trim().toLowerCase());
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || a.name.localeCompare(b.name, "ru");
    });
}

function otherWarehouses(warehouses: WarehouseStockLine[]): WarehouseStockLine[] {
  return warehouses.filter((w) => !MAIN_WAREHOUSES.some((m) => m.match(w.name)));
}

function isSizeToken(value: string): boolean {
  const s = value.trim();
  if (!s) return false;
  if (/^\d{2,3}(?:\/\d{2,3})?[A-Za-zА-Яа-я]?$/i.test(s)) return true;
  if (/^(?:XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL)$/i.test(s)) return true;
  if (/^\d{2,3}\s*-\s*\d{2,3}$/.test(s)) return true;
  return false;
}

function topSectionOf(category: string): string {
  const top = category.split(/[/\\]/)[0]?.trim() || "";
  return top;
}

function categoryParts(category: string): string[] {
  return category.split(/[/\\]/).map((p) => p.trim()).filter(Boolean);
}

function subSectionOf(category: string): string {
  return categoryParts(category)[1] || "";
}

function subCategoryOf(category: string): string {
  const parts = categoryParts(category);
  if (parts.length <= 1) return "";
  return parts.slice(1).join(" / ");
}

function normalizeSectionKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/^\d+\.\s*/, "")
    .trim();
}

/** Только 5 разделов зала. Остальные папки МС игнорируем до разбора дерева. */
function resolveHallSection(category: string): string | null {
  const key = normalizeSectionKey(topSectionOf(category));
  if (!key) return null;
  const exact = SECTION_ORDER.find((s) => normalizeSectionKey(s) === key);
  if (exact) return exact;
  // Длинные ключи раньше («верхняя одежда» > «одежда»).
  const ranked = [...SECTION_ORDER].sort(
    (a, b) => normalizeSectionKey(b).length - normalizeSectionKey(a).length
  );
  return (
    ranked.find((s) => {
      const sk = normalizeSectionKey(s);
      return key === sk || key.startsWith(`${sk} `) || key.startsWith(`${sk}/`);
    }) ?? null
  );
}

function matchSectionOrder(name: string): number {
  const idx = SECTION_ORDER.findIndex((s) => normalizeSectionKey(s) === normalizeSectionKey(name));
  return idx === -1 ? 100 : idx;
}

function parseProduct(product: Product): ParsedProduct | null {
  const section = resolveHallSection(product.category || "");
  if (!section) return null;
  const subSection = subSectionOf(product.category || "");
  const subCategory = subCategoryOf(product.category || "");
  const m = product.name.match(/^(.*?)\s*\((.*)\)\s*$/);
  if (!m) {
    return {
      product,
      section,
      subSection,
      subCategory,
      baseName: product.name.trim() || "Без названия",
      color: "",
      size: "",
      variation: "",
      modsLabel: "",
    };
  }
  const baseName = m[1]!.trim() || product.name.trim();
  const mods = m[2]!
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  const size = mods.find(isSizeToken) ?? "";
  const nonSize = mods.filter((part) => part !== size);
  const color = nonSize[0] ?? "";
  const variation = nonSize.slice(1).join(", ");
  return {
    product,
    section,
    subSection,
    subCategory,
    baseName,
    color,
    size,
    variation,
    modsLabel: mods.join(", "),
  };
}

async function withStock(rows: Product[]): Promise<Product[]> {
  const need = rows.filter((p) => !p.warehouses?.length);
  if (!need.length) return rows;
  const map = new Map<string, WarehouseStockLine[]>();
  await Promise.all(
    need.map(async (p) => {
      try {
        const data = await api.get<{ warehouses: WarehouseStockLine[] }>(
          `/catalog/${encodeURIComponent(p.id)}/stock?cache=1`
        );
        map.set(p.id, data.warehouses ?? []);
      } catch {
        map.set(p.id, []);
      }
    })
  );
  return rows.map((p) => ({
    ...p,
    warehouses: p.warehouses?.length ? p.warehouses : map.get(p.id) ?? [],
  }));
}

function stockSum(product: Product): number {
  return (product.warehouses ?? []).reduce((s, w) => s + (w.available || 0), 0);
}

export function ProductCheck() {
  const { activeStore } = useStore();
  const [q, setQ] = useState("");
  const [section, setSection] = useState("");
  const [color, setColor] = useState("");
  const [size, setSize] = useState("");
  const [variation, setVariation] = useState("");
  const [subCategory, setSubCategory] = useState("");
  const [inStockOnly, setInStockOnly] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const sectionOptions = SECTION_ORDER;
  const [products, setProducts] = useState<Product[]>([]);
  const [folderSubs, setFolderSubs] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());
  const [openBases, setOpenBases] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [printingId, setPrintingId] = useState<string | null>(null);
  const [printError, setPrintError] = useState<string | null>(null);

  /** Печать бирки (П6): PDF из печатных форм МойСклад открывается для печати. */
  async function printLabel(product: Product) {
    setPrintingId(product.id);
    setPrintError(null);
    try {
      const blob = await apiBlob("POST", `/catalog/${encodeURIComponent(product.id)}/label`, {
        count: 1,
      });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      setPrintError(e instanceof Error ? e.message : "Не удалось получить бирку");
    } finally {
      setPrintingId(null);
    }
  }

  useEffect(() => {
    if (USE_MOCK) return;
    api
      .get<{ folders?: Array<{ name: string; path: string; parentPath: string | null; level: number }> }>(
        "/catalog/references"
      )
      .then((refs) => {
        const map: Record<string, string[]> = {};
        for (const hall of SECTION_ORDER) {
          const names = (refs.folders ?? [])
            .filter((f) => {
              if (f.level !== 2) return false;
              const parent = f.parentPath || topSectionOf(f.path);
              return resolveHallSection(parent) === hall;
            })
            .map((f) => f.name.trim())
            .filter(Boolean);
          map[hall] = [...new Set(names)].sort((a, b) => a.localeCompare(b, "ru"));
        }
        setFolderSubs(map);
      })
      .catch(() => setFolderSubs({}));
  }, []);

  useEffect(() => {
    if (USE_MOCK) {
      setError("Включён mock-режим — реальные остатки недоступны. Перезапустите без VITE_USE_MOCK.");
      setProducts([]);
      return;
    }
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      setExpandedId(null);
      try {
        const browsing = !q.trim() && !section;
        const rows = await api.getQuery<Product[]>("/catalog/search", {
          q: q.trim() || undefined,
          category: section || undefined,
          store: activeStore,
          browse: browsing ? 1 : undefined,
          limit: browsing ? 120 : 400,
        });
        const withWh = await withStock(rows);
        setProducts(withWh);
        // При текстовом поиске открываем группы, где есть совпадения.
        if (q.trim() && withWh.length) {
          const keys = withWh
            .map(parseProduct)
            .filter((row): row is ParsedProduct => row != null)
            .map((row) => (section ? row.subSection || EMPTY_SUBSECTION : row.section));
          setOpenSections(new Set(keys));
          setOpenBases(new Set());
        } else {
          setOpenSections(new Set());
          setOpenBases(new Set());
        }
      } catch (e) {
        setProducts([]);
        setError(e instanceof Error ? e.message : "Не удалось загрузить каталог");
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [q, section, activeStore]);

  const parsed = useMemo(
    () => products.map(parseProduct).filter((row): row is ParsedProduct => row != null),
    [products]
  );

  /** Подразделы выбранного раздела зала — из МС + из загруженных товаров. */
  const subSectionOptions = useMemo(() => {
    if (!section) return [] as string[];
    const names = new Set<string>(folderSubs[section] ?? []);
    for (const row of parsed) {
      if (normalizeSectionKey(row.section) !== normalizeSectionKey(section)) continue;
      if (row.subSection) names.add(row.subSection);
    }
    return [...names].sort((a, b) => a.localeCompare(b, "ru"));
  }, [section, folderSubs, parsed]);

  const filterOptions = useMemo(() => {
    const colors = new Set<string>();
    const sizes = new Set<string>();
    const variations = new Set<string>();
    for (const row of parsed) {
      if (section && normalizeSectionKey(row.section) !== normalizeSectionKey(section)) continue;
      if (subCategory) {
        const key = row.subSection || EMPTY_SUBSECTION;
        if (key !== subCategory) continue;
      }
      if (row.color) colors.add(row.color);
      if (row.size) sizes.add(row.size);
      if (row.variation) variations.add(row.variation);
    }
    const sortRu = (a: string, b: string) => a.localeCompare(b, "ru", { numeric: true });
    return {
      colors: [...colors].sort(sortRu),
      sizes: [...sizes].sort(sortRu),
      variations: [...variations].sort(sortRu),
    };
  }, [parsed, section, subCategory]);

  const filtered = useMemo(() => {
    return parsed.filter((row) => {
      if (section && normalizeSectionKey(row.section) !== normalizeSectionKey(section)) return false;
      if (subCategory) {
        const key = row.subSection || EMPTY_SUBSECTION;
        if (key !== subCategory) return false;
      }
      if (color && row.color !== color) return false;
      if (size && row.size !== size) return false;
      if (variation && row.variation !== variation) return false;
      if (inStockOnly && stockSum(row.product) <= 0) return false;
      return true;
    });
  }, [parsed, section, color, size, variation, subCategory, inStockOnly]);

  const tree = useMemo(() => {
    type BaseGroup = { baseName: string; rows: ParsedProduct[] };
    type SectionGroup = { section: string; bases: BaseGroup[]; variantCount: number };
    // В режиме раздела — дерево по подразделам; иначе — по 5 разделам зала.
    const byGroup = new Map<string, Map<string, ParsedProduct[]>>();
    for (const row of filtered) {
      const groupKey = section ? row.subSection || EMPTY_SUBSECTION : row.section;
      const secMap = byGroup.get(groupKey) ?? new Map<string, ParsedProduct[]>();
      secMap.set(row.baseName, [...(secMap.get(row.baseName) ?? []), row]);
      byGroup.set(groupKey, secMap);
    }
    const sections = [...byGroup.entries()]
      .map(([sec, basesMap]): SectionGroup => {
        const bases = [...basesMap.entries()]
          .map(([baseName, rows]) => ({
            baseName,
            rows: rows.sort((a, b) => a.product.name.localeCompare(b.product.name, "ru")),
          }))
          .sort((a, b) => a.baseName.localeCompare(b.baseName, "ru"));
        return {
          section: sec,
          bases,
          variantCount: bases.reduce((n, b) => n + b.rows.length, 0),
        };
      })
      .sort((a, b) => {
        if (section) {
          if (a.section === EMPTY_SUBSECTION) return 1;
          if (b.section === EMPTY_SUBSECTION) return -1;
          return a.section.localeCompare(b.section, "ru");
        }
        return (
          matchSectionOrder(a.section) - matchSectionOrder(b.section) ||
          a.section.localeCompare(b.section, "ru")
        );
      });
    return sections;
  }, [filtered, section]);

  const extraActive = Boolean(color || size || variation || inStockOnly);

  function resetExtraFilters() {
    setColor("");
    setSize("");
    setVariation("");
    setInStockOnly(false);
  }

  function selectHallSection(next: string) {
    setSection(next);
    setSubCategory("");
    resetExtraFilters();
    setOpenSections(new Set());
    setOpenBases(new Set());
  }

  function toggleSection(name: string) {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleBase(sectionName: string, baseName: string) {
    const key = `${sectionName}::${baseName}`;
    setOpenBases((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <Boxes className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Поиск товара</h1>
      </div>
      <p className="text-mute text-sm mb-5">
        Разделы зала · модели без цвета/размера · клик по вариации — остатки по складам.
      </p>

      <div className={`flex gap-1.5 flex-wrap ${section ? "mb-2" : "mb-3"}`}>
        <button
          type="button"
          onClick={() => selectHallSection("")}
          className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
            !section
              ? "bg-gold/15 text-gold-soft border border-gold/40"
              : "text-mute hover:bg-ink-800 border border-transparent"
          }`}
        >
          Все разделы
        </button>
        {sectionOptions.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => selectHallSection(value)}
            className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
              section && normalizeSectionKey(section) === normalizeSectionKey(value)
                ? "bg-gold/15 text-gold-soft border border-gold/40"
                : "text-mute hover:bg-ink-800 border border-transparent"
            }`}
          >
            {value}
          </button>
        ))}
      </div>

      {section && (
        <div className="flex gap-1.5 mb-3 flex-wrap">
          <button
            type="button"
            onClick={() => {
              setSubCategory("");
              setOpenSections(new Set());
              setOpenBases(new Set());
            }}
            className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
              !subCategory
                ? "bg-white/10 text-white border border-white/25"
                : "text-mute hover:bg-ink-800 border border-transparent"
            }`}
          >
            Все подразделы
          </button>
          {subSectionOptions.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setSubCategory(value);
                setOpenSections(new Set([value]));
                setOpenBases(new Set());
              }}
              className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
                subCategory === value
                  ? "bg-white/10 text-white border border-white/25"
                  : "text-mute hover:bg-ink-800 border border-transparent"
              }`}
            >
              {value}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-2 mb-3 flex-wrap">
        <div className="relative flex-1 min-w-0 w-full sm:min-w-[220px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input
            className="input pl-9 pr-10"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Артикул, цвет, название или штрихкод…"
            autoComplete="off"
          />
          {loading && (
            <Loader2 size={17} className="absolute right-3 top-1/2 -translate-y-1/2 text-mute animate-spin" />
          )}
        </div>
        <button
          type="button"
          onClick={() => setScannerOpen(true)}
          className="inline-flex items-center justify-center gap-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mute-soft hover:border-gold/40 hover:text-white"
          aria-label="Сканировать штрихкод"
        >
          <ScanLine size={17} className="text-gold" />
          <span className="hidden sm:inline">Сканер</span>
        </button>
      </div>

      <div className="card mb-4 p-0 overflow-hidden">
        <button
          type="button"
          onClick={() => setFiltersOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-ink-800/40"
        >
          <Filter size={15} className="text-mute" />
          <span className="text-[13px] font-medium text-white flex-1">
            Фильтры{extraActive ? " · активны" : ""}
          </span>
          {extraActive && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                resetExtraFilters();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.stopPropagation();
                  resetExtraFilters();
                }
              }}
              className="inline-flex items-center gap-1 text-[12px] text-mute hover:text-white px-2"
            >
              <X size={13} /> Сбросить
            </span>
          )}
          {filtersOpen ? <ChevronUp size={16} className="text-mute" /> : <ChevronDown size={16} className="text-mute" />}
        </button>
        {filtersOpen && (
          <div className="px-3 pb-3 flex flex-wrap gap-2 items-end border-t border-ink-800 pt-3">
            <div className="min-w-[140px] flex-1">
              <div className="field-label">Цвет</div>
              <select className="input py-2 text-sm" value={color} onChange={(e) => setColor(e.target.value)}>
                <option value="">Все цвета</option>
                {filterOptions.colors.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[120px] flex-1">
              <div className="field-label">Размер</div>
              <select className="input py-2 text-sm" value={size} onChange={(e) => setSize(e.target.value)}>
                <option value="">Все размеры</option>
                {filterOptions.sizes.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[140px] flex-1">
              <div className="field-label">Вариация</div>
              <select
                className="input py-2 text-sm"
                value={variation}
                onChange={(e) => setVariation(e.target.value)}
              >
                <option value="">Все вариации</option>
                {filterOptions.variations.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
            <label className="inline-flex items-center gap-2 pb-2 text-[13px] text-mute-soft cursor-pointer select-none">
              <input
                type="checkbox"
                className="rounded border-ink-600"
                checked={inStockOnly}
                onChange={(e) => setInStockOnly(e.target.checked)}
              />
              Только в наличии
            </label>
          </div>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          {error}
        </div>
      )}

      <div className="space-y-3">
        {tree.map((sec) => {
          const sectionOpen = openSections.has(sec.section);
          return (
            <section key={sec.section} className="card p-0 overflow-hidden">
              <button
                type="button"
                className="w-full px-4 py-3 flex items-center gap-2 text-left hover:bg-ink-800/50"
                onClick={() => toggleSection(sec.section)}
              >
                {sectionOpen ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
                <span className="text-white font-semibold flex-1">{sec.section}</span>
                <span className="chip bg-ink-700 text-mute">
                  {sec.bases.length} мод. · {sec.variantCount}
                </span>
              </button>

              {sectionOpen && (
                <div className="border-t border-ink-700 divide-y divide-ink-800">
                  {sec.bases.map((base) => {
                    const baseKey = `${sec.section}::${base.baseName}`;
                    const baseOpen = openBases.has(baseKey);
                    const totalStock = base.rows.reduce((n, r) => n + stockSum(r.product), 0);
                    return (
                      <div key={baseKey}>
                        <button
                          type="button"
                          className="w-full px-4 py-2.5 flex items-center gap-2 text-left hover:bg-ink-800/40"
                          onClick={() => toggleBase(sec.section, base.baseName)}
                        >
                          {baseOpen ? (
                            <ChevronDown size={15} className="text-mute" />
                          ) : (
                            <ChevronRight size={15} className="text-mute" />
                          )}
                          <span className="text-white font-medium flex-1 leading-snug">{base.baseName}</span>
                          <span className={`text-[12px] tabular-nums ${qtyClass(totalStock)}`}>
                            {fmtQty(totalStock)}
                          </span>
                          <span className="chip bg-ink-800 text-mute">{base.rows.length}</span>
                        </button>

                        {baseOpen && (
                          <div className="overflow-x-auto border-t border-ink-800 bg-ink-950/30">
                            <table className="w-full text-sm min-w-[720px]">
                              <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
                                <tr>
                                  <th className="text-left px-4 py-2 font-medium">Вариация</th>
                                  {MAIN_WAREHOUSES.map((w) => (
                                    <th
                                      key={w.id}
                                      className="text-right px-3 py-2 font-medium whitespace-nowrap w-[110px]"
                                    >
                                      {w.label}
                                    </th>
                                  ))}
                                  <th className="text-right px-4 py-2 font-medium whitespace-nowrap w-[90px]">
                                    Цена
                                  </th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-ink-800">
                                {base.rows.map((row) => {
                                  const product = row.product;
                                  const warehouses = product.warehouses ?? [];
                                  const expanded = expandedId === product.id;
                                  const others = otherWarehouses(warehouses);
                                  const leftovers = expanded ? leftoverWarehouses(warehouses) : [];
                                  const modBits = [row.color, row.size, row.variation].filter(Boolean);
                                  return (
                                    <Fragment key={product.id}>
                                      <tr
                                        className="hover:bg-ink-800/40 cursor-pointer"
                                        onClick={() => setExpandedId(expanded ? null : product.id)}
                                      >
                                        <td className="px-4 py-3 align-top">
                                          <div className="text-white font-medium leading-snug">
                                            {modBits.length ? modBits.join(" · ") : row.modsLabel || "—"}
                                          </div>
                                          <div className="text-[12px] text-mute mt-0.5">
                                            {product.sku || "—"}
                                            {product.barcode ? ` · ${product.barcode}` : ""}
                                            {row.subCategory ? ` · ${row.subCategory}` : ""}
                                            {others.length > 0 && (
                                              <span className="text-gold-soft">
                                                {" "}
                                                · ещё {others.length} склад
                                                {others.length === 1 ? "" : others.length < 5 ? "а" : "ов"}
                                              </span>
                                            )}
                                          </div>
                                        </td>
                                        {MAIN_WAREHOUSES.map((w) => {
                                          const n = qtyAt(warehouses, w.match);
                                          return (
                                            <td
                                              key={w.id}
                                              className={`px-3 py-3 text-right tabular-nums align-top ${qtyClass(n)}`}
                                            >
                                              {fmtQty(n)}
                                            </td>
                                          );
                                        })}
                                        <td className="px-4 py-3 text-right text-gold-soft font-semibold whitespace-nowrap align-top">
                                          {money(product.price)}
                                        </td>
                                      </tr>
                                      {expanded && (
                                        <tr className="bg-ink-900/60">
                                          <td colSpan={5} className="px-4 py-3">
                                            <div className="flex items-center gap-3 mb-2">
                                              <div className="field-label mb-0 flex-1">Все склады / положения</div>
                                              <button
                                                type="button"
                                                disabled={printingId === product.id}
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  void printLabel(product);
                                                }}
                                                className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-[13px] text-mute-soft hover:border-gold/40 hover:text-white disabled:opacity-50"
                                              >
                                                {printingId === product.id ? (
                                                  <Loader2 size={14} className="animate-spin" />
                                                ) : (
                                                  <Printer size={14} className="text-gold" />
                                                )}
                                                Печать бирки
                                              </button>
                                            </div>
                                            {printError && expandedId === product.id && (
                                              <div className="mb-2 text-[12px] text-red-300">{printError}</div>
                                            )}
                                            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                                              {EXPANDED_COLUMNS.map((col) => (
                                                <div key={col.id} className="space-y-1.5">
                                                  {col.slots.map((slot) => {
                                                    const n = qtyAt(warehouses, slot.match);
                                                    return (
                                                      <div
                                                        key={slot.label}
                                                        className="flex justify-between gap-3 rounded-md border border-ink-700 px-2.5 py-1.5 text-[13px]"
                                                      >
                                                        <span className="text-mute-soft truncate">
                                                          {slot.label}
                                                        </span>
                                                        <span
                                                          className={`tabular-nums shrink-0 ${qtyClass(n)}`}
                                                        >
                                                          {fmtQty(n)}
                                                        </span>
                                                      </div>
                                                    );
                                                  })}
                                                </div>
                                              ))}
                                            </div>
                                            {leftovers.length > 0 && (
                                              <div className="mt-3 grid sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
                                                {leftovers.map((w) => (
                                                  <div
                                                    key={w.warehouseMsId}
                                                    className="flex justify-between gap-3 rounded-md border border-ink-700 px-2.5 py-1.5 text-[13px]"
                                                  >
                                                    <span className="text-mute-soft truncate">{w.name}</span>
                                                    <span
                                                      className={`tabular-nums shrink-0 ${qtyClass(w.available)}`}
                                                    >
                                                      {fmtQty(w.available)}
                                                    </span>
                                                  </div>
                                                ))}
                                              </div>
                                            )}
                                          </td>
                                        </tr>
                                      )}
                                    </Fragment>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
        {!loading && tree.length === 0 && !error && (
          <div className="card py-10 text-center text-mute">Товары не найдены</div>
        )}
      </div>

      {scannerOpen && (
        <BarcodeScannerModal
          onDetected={(code) => {
            setQ(code);
            setScannerOpen(false);
          }}
          onClose={() => setScannerOpen(false)}
        />
      )}
    </div>
  );
}
