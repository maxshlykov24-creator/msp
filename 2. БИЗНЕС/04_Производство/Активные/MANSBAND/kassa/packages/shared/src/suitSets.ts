import { isHalfSetWarehouse, suitModelId, suitSizeNumber, suitTitle } from "./constants.js";
import type {
  SuitCompleteness,
  SuitLine,
  SuitModel,
  SuitOrphan,
  SuitPart,
  SuitSizeRow,
} from "./types.js";

/**
 * Комплектность костюмов — чистый расчёт, без базы и МойСклада.
 *
 * Костюма как товара в МойСкладе нет: заведены пиджак, брюки и жилет
 * отдельными видами, а связывает их характеристика «Вариация». Поэтому костюм
 * собирается вычислением, а не комплектом МС: комплект не описывает полупарк,
 * а коды маркировки всё равно живут на изделиях.
 *
 * Три категории по формулировке владельца:
 *  - цельный костюм: все части состава в одном размере;
 *  - правильный полупарк: части есть, размеры расходятся в пределах допуска —
 *    такой костюм продаётся как костюм;
 *  - неправильный полупарк: пары нет вовсе или расхождение вне допуска.
 *
 * Функция вынесена в shared, чтобы её можно было прогнать на живой выгрузке
 * остатков без поднятия базы.
 */

/** Строка остатка: одно изделие на одном складе. */
export interface SuitStockLine {
  msId: string;
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

/**
 * Ключ модели без цвета и узора: внутри вариации они совпадают не всегда (цвет
 * только у 70% моделей), поэтому для отображения берутся по пиджаку, а склейку
 * держат вариация, крой, ростовка и линия.
 */
export function suitModelKey(line: SuitStockLine): string {
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

function countsOf(lines: SuitStockLine[]): Counts {
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

/** Модель костюма по остаткам одной группы. Не костюм — null. */
export function buildSuitModel(
  lines: SuitStockLine[],
  halfSetLines: SuitStockLine[],
  tolerance: number
): SuitModel | null {
  const jackets = lines.filter((l) => l.part === "jacket");
  // Модели без пиджака вообще — самостоятельные брюки из справочника составов,
  // а не разбитый костюм. В комплектность их не берём.
  const anyJacket = jackets.length > 0 || halfSetLines.some((l) => l.part === "jacket");
  if (!anyJacket) return null;

  const sample = jackets[0] ?? lines[0] ?? halfSetLines[0]!;
  const hasVest =
    lines.some((l) => l.part === "vest") || halfSetLines.some((l) => l.part === "vest");
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

  const byPartSize = new Map<string, SuitStockLine[]>();
  for (const line of lines) {
    const key = `${line.part}|${line.size}`;
    byPartSize.set(key, [...(byPartSize.get(key) ?? []), line]);
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
        sortSizes([...counts.get(other)!.keys()].filter((s) => left(counts, other, s) > 0)).slice(
          0,
          3
        )
      );
      const pairLocations = missing.flatMap((other) =>
        (byPartSize.get(`${other}|${size}`) ?? []).map((l) => ({
          part: other,
          size: l.size,
          warehouse: l.warehouse,
          qty: l.qty,
          msId: l.msId,
        }))
      );
      orphans.push({ part, qty, missing, nearestSizes: [...new Set(nearestSizes)], pairLocations });
    }
    const atSize = countsOf(lines.filter((l) => l.size === size));
    rows.push({
      size,
      parts: {
        jacket: atSize.get("jacket")!.get(size) ?? 0,
        trousers: atSize.get("trousers")!.get(size) ?? 0,
        vest: atSize.get("vest")!.get(size) ?? 0,
      },
      whole: whole.get(size) ?? 0,
      tolerant: tolerant.get(size) ?? 0,
      orphans,
    });
  }

  const wholeTotal = [...whole.values()].reduce((a, b) => a + b, 0);
  const tolerantTotal = [...tolerant.values()].reduce((a, b) => a + b, 0);
  const orphanTotal = rows.reduce((sum, row) => sum + row.orphans.reduce((s, o) => s + o.qty, 0), 0);

  return {
    modelId: suitModelKey(sample),
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

export interface CompletenessOptions {
  tolerance: number;
  /** Склад МойСклад; без него считаем по всем складам, кроме полупарков. */
  warehouse?: string;
  /** Поиск по вариации, цвету, названию. */
  q?: string;
  limit?: number;
}

export function computeCompleteness(
  lines: SuitStockLine[],
  options: CompletenessOptions
): SuitCompleteness {
  const warehouse = options.warehouse?.trim() || undefined;
  const scoped = warehouse
    ? lines.filter((l) => l.warehouse === warehouse)
    : lines.filter((l) => !isHalfSetWarehouse(l.warehouse));
  // Остаток физических складов полупарков считается отдельно: он уже признан
  // некомплектом руками, и смешивать его с вычисленным нельзя — изделие попало
  // бы в цифры дважды.
  const halfSets = lines.filter((l) => isHalfSetWarehouse(l.warehouse));

  const grouped = new Map<string, SuitStockLine[]>();
  for (const line of scoped) {
    const key = suitModelKey(line);
    grouped.set(key, [...(grouped.get(key) ?? []), line]);
  }
  const halfSetsByModel = new Map<string, SuitStockLine[]>();
  for (const line of halfSets) {
    const key = suitModelKey(line);
    halfSetsByModel.set(key, [...(halfSetsByModel.get(key) ?? []), line]);
  }

  const needle = (options.q ?? "").trim().toLowerCase();
  let models: SuitModel[] = [];
  for (const [key, group] of grouped) {
    const model = buildSuitModel(group, halfSetsByModel.get(key) ?? [], options.tolerance);
    if (!model) continue;
    if (needle) {
      const haystack = [
        model.variation,
        model.title,
        model.color,
        model.pattern,
        model.fit,
        model.height,
      ]
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
  if (options.limit && options.limit > 0) models = models.slice(0, options.limit);

  return { warehouse: warehouse ?? null, tolerance: options.tolerance, totals, models };
}
