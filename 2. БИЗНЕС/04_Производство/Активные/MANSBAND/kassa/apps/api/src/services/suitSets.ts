import { and, gt, isNotNull, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock } from "../db/schema.js";
import {
  isHalfSetWarehouse,
  STORE_TO_WAREHOUSE,
  suitModelId,
  suitSizeNumber,
  suitTitle,
} from "@kassa/shared";
import type {
  SuitCompleteness,
  SuitLine,
  SuitModel,
  SuitOrphan,
  SuitPart,
  SuitSizeRow,
} from "@kassa/shared";
import { getAppSettings } from "./settings.js";

/**
 * Комплектность костюмов. Костюма в МойСкладе нет: заведены пиджак, брюки и
 * жилет отдельными видами, а связывает их характеристика «Вариация». Поэтому
 * костюм собирается здесь вычислением, а не комплектом МС.
 *
 * Три категории по формулировке владельца:
 *  - цельный костюм: все части состава в одном размере;
 *  - правильный полупарк: части есть, размеры расходятся в пределах допуска —
 *    такой костюм продаётся;
 *  - неправильный полупарк: пары нет вовсе или расхождение вне допуска.
 *
 * Остаток физических складов полупарков считается отдельно: он уже признан
 * некомплектом руками, и смешивать его с вычисленным нельзя — изделие попало бы
 * в цифры дважды.
 */

interface StockLine {
  msId: string;
  name: string;
  article: string | null;
  variation: string;
  size: string;
  height: string;
  color: string | null;
  pattern: string | null;
  fit: string;
  part: SuitPart;
  line: SuitLine;
  warehouse: string;
  qty: number;
}

const COMPOSITION_ORDER: SuitPart[] = ["jacket", "trousers", "vest"];

async function loadStockLines(): Promise<StockLine[]> {
  const rows = await db
    .select({
      msId: products.msId,
      name: products.name,
      article: products.article,
      variation: products.variation,
      size: products.size,
      height: products.height,
      color: products.color,
      pattern: products.pattern,
      fit: products.fit,
      part: products.suitPart,
      line: products.suitLine,
      warehouse: stock.warehouseName,
      qty: stock.quantity,
    })
    .from(products)
    .innerJoin(stock, sql`${stock.productMsId} = ${products.msId}`)
    .where(
      and(isNotNull(products.suitPart), isNotNull(products.variation), gt(stock.quantity, "0"))
    );

  return rows
    .map((r) => ({
      msId: r.msId,
      name: r.name,
      article: r.article,
      variation: (r.variation ?? "").trim(),
      size: (r.size ?? "").trim(),
      height: (r.height ?? "").trim(),
      color: r.color,
      pattern: r.pattern,
      fit: (r.fit ?? "").trim(),
      part: r.part as SuitPart,
      line: (r.line as SuitLine) ?? "regular",
      warehouse: r.warehouse,
      qty: Math.max(0, Math.floor(Number(r.qty) || 0)),
    }))
    .filter((r) => r.variation && r.qty > 0);
}

/**
 * Ключ модели без цвета и узора: внутри вариации они совпадают не всегда (цвет
 * только у 70% моделей), поэтому для отображения берутся по пиджаку, а склейку
 * держат вариация, крой, ростовка и линия.
 */
function modelKey(line: StockLine): string {
  return suitModelId({
    variation: line.variation,
    color: "",
    pattern: "",
    fit: line.fit,
    height: line.height,
    line: line.line,
  });
}

/** Размеры по возрастанию; нечисловые уходят в конец в алфавитном порядке. */
function sortSizes(sizes: string[]): string[] {
  return [...sizes].sort((a, b) => {
    const na = suitSizeNumber(a);
    const nb = suitSizeNumber(b);
    if (na != null && nb != null) return na - nb;
    if (na != null) return -1;
    if (nb != null) return 1;
    return a.localeCompare(b, "ru");
  });
}

type Counts = Map<SuitPart, Map<string, number>>;

function countsOf(lines: StockLine[]): Counts {
  const counts: Counts = new Map();
  for (const part of COMPOSITION_ORDER) counts.set(part, new Map());
  for (const line of lines) {
    const bySize = counts.get(line.part)!;
    bySize.set(line.size, (bySize.get(line.size) ?? 0) + line.qty);
  }
  return counts;
}

function take(counts: Counts, part: SuitPart, size: string, qty: number): void {
  const bySize = counts.get(part)!;
  bySize.set(size, Math.max(0, (bySize.get(size) ?? 0) - qty));
}

function left(counts: Counts, part: SuitPart, size: string): number {
  return counts.get(part)!.get(size) ?? 0;
}

/**
 * Размеры парной части в порядке близости к размеру пиджака: сначала точный,
 * затем сдвиг на единицу, и так до допуска. Нечисловой размер парой не считаем.
 */
function candidateSizes(part: SuitPart, counts: Counts, size: string, tolerance: number): string[] {
  const base = suitSizeNumber(size);
  if (base == null) return left(counts, part, size) > 0 ? [size] : [];
  return [...counts.get(part)!.keys()]
    .filter((other) => {
      if (left(counts, part, other) <= 0) return false;
      const n = suitSizeNumber(other);
      return n != null && Math.abs(n - base) <= tolerance;
    })
    .sort((a, b) => Math.abs(suitSizeNumber(a)! - base) - Math.abs(suitSizeNumber(b)! - base));
}

function buildModel(
  lines: StockLine[],
  halfSetLines: StockLine[],
  tolerance: number
): SuitModel | null {
  const jackets = lines.filter((l) => l.part === "jacket");
  // Модели без пиджака вообще — самостоятельные брюки из справочника составов,
  // а не разбитый костюм. В комплектность их не берём.
  const anyJacket = jackets.length > 0 || halfSetLines.some((l) => l.part === "jacket");
  if (!anyJacket) return null;

  const sample = jackets[0] ?? lines[0] ?? halfSetLines[0]!;
  const hasVest = lines.some((l) => l.part === "vest") || halfSetLines.some((l) => l.part === "vest");
  const composition: SuitPart[] = hasVest ? ["jacket", "trousers", "vest"] : ["jacket", "trousers"];

  // Цвет и узор — по пиджаку: внутри вариации части расходятся, и в такой
  // модели цифры считаем, но помечаем расхождение.
  const jacketSample = jackets[0] ?? sample;
  const colors = new Set(lines.map((l) => (l.color ?? "").trim()).filter(Boolean));
  const patterns = new Set(lines.map((l) => (l.pattern ?? "").trim()).filter(Boolean));

  const counts = countsOf(lines);
  const sizes = sortSizes([
    ...new Set(lines.filter((l) => composition.includes(l.part)).map((l) => l.size)),
  ]);

  const whole = new Map<string, number>();
  for (const size of sizes) {
    const qty = Math.min(...composition.map((part) => left(counts, part, size)));
    if (qty > 0) {
      whole.set(size, qty);
      for (const part of composition) take(counts, part, size, qty);
    }
  }

  // Правильный полупарк: пиджак ведущий, к нему подбираем ближайшие по размеру
  // брюки и жилет в пределах допуска.
  const tolerant = new Map<string, number>();
  if (tolerance > 0) {
    for (const size of sizes) {
      while (left(counts, "jacket", size) > 0) {
        const picks: Array<{ part: SuitPart; size: string }> = [];
        for (const part of composition) {
          if (part === "jacket") continue;
          const candidate = candidateSizes(part, counts, size, tolerance)[0];
          if (!candidate) break;
          picks.push({ part, size: candidate });
        }
        if (picks.length !== composition.length - 1) break;
        take(counts, "jacket", size, 1);
        for (const pick of picks) take(counts, pick.part, pick.size, 1);
        tolerant.set(size, (tolerant.get(size) ?? 0) + 1);
      }
    }
  }

  const byPartSizeWarehouse = new Map<string, StockLine[]>();
  for (const line of lines) {
    const key = `${line.part}|${line.size}`;
    byPartSizeWarehouse.set(key, [...(byPartSizeWarehouse.get(key) ?? []), line]);
  }

  const rows: SuitSizeRow[] = [];
  for (const size of sizes) {
    const orphans: SuitOrphan[] = [];
    for (const part of composition) {
      const qty = left(counts, part, size);
      if (qty <= 0) continue;
      const missing = composition.filter(
        (other) => other !== part && candidateSizes(other, counts, size, tolerance).length === 0
      );
      const nearestSizes = missing.flatMap((other) =>
        sortSizes([...counts.get(other)!.keys()].filter((s) => left(counts, other, s) > 0)).slice(0, 3)
      );
      const pairLocations = missing.flatMap((other) =>
        (byPartSizeWarehouse.get(`${other}|${size}`) ?? []).map((l) => ({
          part: other,
          size: l.size,
          warehouse: l.warehouse,
          qty: l.qty,
          msId: l.msId,
        }))
      );
      orphans.push({ part, qty, missing, nearestSizes: [...new Set(nearestSizes)], pairLocations });
    }
    const partsAtSize = countsOf(lines.filter((l) => l.size === size));
    rows.push({
      size,
      parts: {
        jacket: partsAtSize.get("jacket")!.get(size) ?? 0,
        trousers: partsAtSize.get("trousers")!.get(size) ?? 0,
        vest: partsAtSize.get("vest")!.get(size) ?? 0,
      },
      whole: whole.get(size) ?? 0,
      tolerant: tolerant.get(size) ?? 0,
      orphans,
    });
  }

  const wholeTotal = [...whole.values()].reduce((a, b) => a + b, 0);
  const tolerantTotal = [...tolerant.values()].reduce((a, b) => a + b, 0);
  const orphanTotal = rows.reduce(
    (sum, row) => sum + row.orphans.reduce((s, o) => s + o.qty, 0),
    0
  );

  return {
    modelId: modelKey(sample),
    variation: sample.variation,
    title: suitTitle({
      hasVest,
      line: sample.line,
      color: jacketSample.color,
      pattern: jacketSample.pattern,
      fit: jacketSample.fit,
    }),
    color: jacketSample.color,
    pattern: jacketSample.pattern,
    fit: jacketSample.fit || null,
    height: sample.height || null,
    line: sample.line,
    composition,
    mixed: colors.size > 1 || patterns.size > 1,
    whole: wholeTotal,
    tolerant: tolerantTotal,
    orphans: orphanTotal,
    onHalfSetWarehouse: halfSetLines.reduce((sum, l) => sum + l.qty, 0),
    sizes: rows.filter((row) => row.whole + row.tolerant + row.orphans.length > 0),
  };
}

export interface CompletenessQuery {
  /** Название склада МойСклад; для консультанта подставляется его магазин. */
  warehouse?: string;
  /** Магазин кассы — переводится в склад по STORE_TO_WAREHOUSE. */
  store?: string;
  /** Поиск по вариации, цвету, названию. */
  q?: string;
  limit?: number;
}

export async function suitCompleteness(query: CompletenessQuery): Promise<SuitCompleteness> {
  const settings = await getAppSettings();
  const tolerance = settings.suitSizeTolerance;
  const warehouse =
    query.warehouse?.trim() ||
    (query.store ? STORE_TO_WAREHOUSE[query.store as keyof typeof STORE_TO_WAREHOUSE] : undefined) ||
    undefined;

  const all = await loadStockLines();
  const scoped = warehouse
    ? all.filter((l) => l.warehouse === warehouse)
    : all.filter((l) => !isHalfSetWarehouse(l.warehouse));
  const halfSets = all.filter((l) => isHalfSetWarehouse(l.warehouse));

  const grouped = new Map<string, StockLine[]>();
  for (const line of scoped) {
    const key = modelKey(line);
    grouped.set(key, [...(grouped.get(key) ?? []), line]);
  }
  const halfSetsByModel = new Map<string, StockLine[]>();
  for (const line of halfSets) {
    const key = modelKey(line);
    halfSetsByModel.set(key, [...(halfSetsByModel.get(key) ?? []), line]);
  }

  const needle = (query.q ?? "").trim().toLowerCase();
  let models: SuitModel[] = [];
  for (const [key, lines] of grouped) {
    const model = buildModel(lines, halfSetsByModel.get(key) ?? [], tolerance);
    if (!model) continue;
    if (needle) {
      const haystack = [model.variation, model.title, model.color, model.pattern, model.fit, model.height]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(needle)) continue;
    }
    models.push(model);
  }

  // Сверху то, что требует работы: больше всего некомплекта.
  models.sort((a, b) => b.orphans - a.orphans || b.whole - a.whole);
  const totals = {
    models: models.length,
    whole: models.reduce((s, m) => s + m.whole, 0),
    tolerant: models.reduce((s, m) => s + m.tolerant, 0),
    orphans: models.reduce((s, m) => s + m.orphans, 0),
    items: scoped.reduce((s, l) => s + l.qty, 0),
  };
  if (query.limit && query.limit > 0) models = models.slice(0, query.limit);

  return { warehouse: warehouse ?? null, tolerance, totals, models };
}

/**
 * Полупарки одной строкой на выгрузку колл-менеджеру: что лежит без пары, в
 * каком размере и чего к нему не хватает.
 */
export async function halfSetList(query: CompletenessQuery): Promise<
  Array<{
    variation: string;
    title: string;
    size: string;
    part: SuitPart;
    qty: number;
    missing: SuitPart[];
    nearestSizes: string[];
  }>
> {
  const { models } = await suitCompleteness({ ...query, limit: undefined });
  const rows: Array<{
    variation: string;
    title: string;
    size: string;
    part: SuitPart;
    qty: number;
    missing: SuitPart[];
    nearestSizes: string[];
  }> = [];
  for (const model of models) {
    for (const size of model.sizes) {
      for (const orphan of size.orphans) {
        rows.push({
          variation: model.variation,
          title: model.title,
          size: size.size,
          part: orphan.part,
          qty: orphan.qty,
          missing: orphan.missing,
          nearestSizes: orphan.nearestSizes,
        });
      }
    }
  }
  return rows;
}
