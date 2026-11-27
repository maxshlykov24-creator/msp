import { isHalfSetWarehouse, SUIT_PART_LABEL, suitModelId, suitSizeNumber, suitTitle } from "./constants.js";
import { catalogSearchTerms, matchesCatalogTerms, variationSearchRank, type CatalogSearchTerm } from "./catalogSearch.js";
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
  /** Название позиции из МойСклада. */
  name?: string;
  article?: string | null;
  code?: string | null;
  barcode?: string | null;
  /** Группа МойСклада — часть строк прайса (сафари, лён, New, детские) распознаётся только по ней. */
  category?: string | null;
}

const COMPOSITION_ORDER: SuitPart[] = ["jacket", "trousers", "vest"];

type JacketFront = NonNullable<SuitModel["jacketFront"]>;

function namedJacketFronts(name: string): JacketFront[] {
  const fronts = new Set<JacketFront>();
  for (const word of name.toLowerCase().split(/[^\p{L}\p{N}]+/u)) {
    const match = /^(одно|двух?)бортн(?:ый|ая|ое|ые|ого|ой|ому|ую|ых|ым|ыми|ом)$/.exec(word);
    if (match) fronts.add(match[1] === "одно" ? "Однобортный" : "Двубортный");
  }
  return [...fronts];
}

function catalogJacketFront(jackets: SuitStockLine[]): JacketFront | null {
  let front: JacketFront | null = null;
  for (const jacket of jackets) {
    const types = namedJacketFronts(jacket.name ?? "");
    if (types.length !== 1 || (front && types[0] !== front)) return null;
    front = types[0]!;
  }
  return front;
}

/**
 * Ключ модели без цвета и узора: внутри вариации они совпадают не всегда (цвет
 * только у 70% моделей), поэтому для отображения берутся по пиджаку, а склейку
 * держат вариация, крой, ростовка и линия.
 */
type ModelKeyFields = Pick<SuitStockLine, "variation" | "fit" | "height" | "line">;

export function suitModelKey(line: ModelKeyFields): string {
  return suitModelId({
    variation: line.variation,
    color: "",
    pattern: "",
    fit: line.fit,
    height: line.height,
    line: line.line,
  });
}

export interface SuitFrontConflict extends ModelKeyFields {
  modelId: string;
  jackets: { msId: string; name: string; size: string; fronts: JacketFront[] }[];
}

/** По одной строке на карточку пиджака из справочника, независимо от остатков. */
export function findSuitFrontConflicts(jackets: (ModelKeyFields & { msId: string; name: string; size: string })[]): SuitFrontConflict[] {
  const groups = new Map<string, SuitFrontConflict>();
  for (const { variation, fit, height, line, msId, name, size } of jackets) {
    if (!variation.trim()) continue;
    const modelId = suitModelKey({ variation, fit, height, line });
    let group = groups.get(modelId);
    if (!group) {
      group = { modelId, variation, fit, height, line, jackets: [] };
      groups.set(modelId, group);
    }
    group.jackets.push({ msId, name, size, fronts: namedJacketFronts(name) });
  }
  const conflicts = [...groups.values()].filter(group =>
    new Set(group.jackets.flatMap(jacket => jacket.fronts)).size > 1
  );
  const order = new Intl.Collator("ru", { numeric: true });
  for (const group of conflicts) {
    group.jackets.sort((a, b) => order.compare(a.size, b.size) || order.compare(a.name, b.name) || a.msId.localeCompare(b.msId));
  }
  return conflicts.sort((a, b) => order.compare(a.modelId, b.modelId));
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

/** Цельные на складе: минимум частей этого размера, без свода между складами. */
function warehouseWholes(
  lines: SuitStockLine[],
  size: string,
  composition: SuitPart[]
): Array<{ name: string; whole: number }> {
  const names = [...new Set(lines.map((line) => line.warehouse).filter(Boolean))];
  const rows: Array<{ name: string; whole: number }> = [];
  for (const name of names) {
    const atWarehouse = lines.filter((line) => line.warehouse === name && line.size === size);
    if (atWarehouse.length === 0) continue;
    const counts = countsOf(atWarehouse);
    rows.push({
      name,
      whole: Math.min(...composition.map((part) => left(counts, part, size))),
    });
  }
  return rows;
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
  tolerance: number,
  /**
   * Поиск по каталогу (блок 3, созвон 09.09): модель с нулевым остатком
   * (везде и по всем размерам) должна остаться в списке, а не исчезнуть.
   * По умолчанию `false` — экран «Костюмы»/полупарки продолжают показывать
   * только размеры с реальным остатком, логика комплектности не меняется.
   */
  keepZeroSizes = false,
  search: CatalogSearchTerm[] = []
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
  const title = suitTitle({ hasVest, line: sample.line, color: jacketSample.color,
    pattern: jacketSample.pattern, fit: jacketSample.fit });
  let matchedKinds: string[] = [];
  if (search.length) {
    const modelFields = [title, sample.height];
    const remaining = search.filter(term => !matchesCatalogTerms([term], modelFields, [sample.variation]));
    // Название изделия, размер и штрихкод должны относиться к одной позиции модели.
    if (remaining.length) {
      const matched = lines.filter(l => matchesCatalogTerms(remaining,
        [l.name, l.category, SUIT_PART_LABEL[l.part], l.size, l.height, `${l.size}/${l.height}`, l.color, l.pattern, l.fit],
        [l.variation, l.article, l.code, l.barcode]));
      if (!matched.length) return null;
      matchedKinds = [...new Set(matched.map(l => l.name?.split(" (")[0]?.trim() || SUIT_PART_LABEL[l.part]))];
    }
  }
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
      warehouses: warehouseWholes([...lines, ...halfSetLines], size, composition),
    });
  }

  const wholeTotal = [...whole.values()].reduce((a, b) => a + b, 0);
  const tolerantTotal = [...tolerant.values()].reduce((a, b) => a + b, 0);
  const orphanTotal = rows.reduce((sum, row) => sum + row.orphans.reduce((s, o) => s + o.qty, 0), 0);

  return {
    modelId: suitModelKey(sample),
    variation: sample.variation,
    title,
    ...(matchedKinds.length ? { matchedKinds } : {}),
    ...(keepZeroSizes ? { jacketFront: catalogJacketFront(jackets) } : {}),
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
    sizes: keepZeroSizes
      ? rows
      : rows.filter((row) => row.whole + row.tolerant + row.orphans.length > 0),
  };
}

/** Позиция чека для счёта костюмов: часть и вариация берутся из каталога. */
export interface SuitCartLine {
  qty: number;
  part: SuitPart | null;
  variation: string | null;
}

/**
 * Костюм в чеке — это проданный костюм: пиджак и брюки одной вариации,
 * размеры могут расходиться (решение владельца 07.09). Жилет входит в тот же
 * костюм и отдельной единицей не считается.
 *
 * Возвращает и число костюмов для САР, и свёрнутое число единиц для UPT:
 * тройка — одна единица, а не три, иначе пороги премий по UPT берутся сами
 * собой на любой продаже костюма.
 */
export function countSuitsInLines(lines: SuitCartLine[]): { suits: number; units: number } {
  const byVariation = new Map<string, Record<SuitPart, number>>();
  let units = 0;
  for (const line of lines) {
    const qty = Math.max(0, Math.floor(line.qty || 0));
    if (qty === 0) continue;
    const variation = (line.variation ?? "").trim();
    if (!line.part || !variation) {
      units += qty;
      continue;
    }
    const group = byVariation.get(variation) ?? { jacket: 0, trousers: 0, vest: 0 };
    group[line.part] += qty;
    byVariation.set(variation, group);
  }

  let suits = 0;
  for (const group of byVariation.values()) {
    const pairs = Math.min(group.jacket, group.trousers);
    suits += pairs;
    const vestsInSuits = Math.min(group.vest, pairs);
    // Костюм — одна единица; части, оставшиеся без пары, считаются штуками.
    units +=
      pairs +
      (group.jacket - pairs) +
      (group.trousers - pairs) +
      (group.vest - vestsInSuits);
  }
  return { suits, units };
}

export interface CompletenessOptions {
  tolerance: number;
  /** Склад МойСклад; без него считаем по всем складам, кроме полупарков. */
  warehouse?: string;
  /** Поиск по вариации, цвету, названию. */
  q?: string;
  limit?: number;
  /** Каталог: не резать размеры и модели с нулевым остатком (см. buildSuitModel). */
  keepZeroSizes?: boolean;
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
  // В каталоге склад определяет остаток. Известные модификации без строки остатка
  // на этом складе всё равно нужны для поиска, состава костюма и его размерного ряда.
  if (options.keepZeroSizes) {
    const known = new Set(scoped.map(l => l.msId));
    for (const line of lines) {
      if (known.has(line.msId)) continue;
      scoped.push({ ...line, warehouse: "", qty: 0 });
      known.add(line.msId);
    }
  }

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

  const search = catalogSearchTerms(options.q ?? "");
  const ranks = new Map<string, number>();
  let models: SuitModel[] = [];
  for (const [key, group] of grouped) {
    const model = buildSuitModel(
      group,
      halfSetsByModel.get(key) ?? [],
      options.tolerance,
      options.keepZeroSizes,
      search
    );
    if (!model) continue;
    if (search.length) ranks.set(key, variationSearchRank(search, model.variation));
    models.push(model);
  }

  // Сверху то, что требует работы: больше всего некомплекта.
  models.sort((a, b) => (ranks.get(a.modelId) ?? 0) - (ranks.get(b.modelId) ?? 0) ||
    b.orphans - a.orphans || b.whole - a.whole || (search.length ? a.modelId.localeCompare(b.modelId) : 0));
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
