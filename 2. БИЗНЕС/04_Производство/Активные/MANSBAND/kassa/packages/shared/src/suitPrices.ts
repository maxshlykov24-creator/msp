import type { SuitPriceMatchInput, SuitPriceRule } from "./types.js";

/**
 * Матрица цен костюмов — прайс владельца от 09.2026 (созвон 09.09): «там где
 * две цены, бери вторую, более высокую». Костюма как товара в МойСклад нет
 * (см. `suitSets.ts`), поэтому цена целиком живёт только здесь, а не в МС.
 *
 * Правила проверяются по порядку, первое совпадение побеждает — поэтому
 * узкие условия (детские, сафари, лён, смокинг, кэжуал, двубортные, 4
 * застёжки) идут раньше общих (обычный однобортный по ростовке/числу
 * предметов). `id` и `label` не переиспользовать — на них ссылается
 * оверрайд `app_settings.suitPriceOverrides`.
 */
export const SUIT_PRICE_RULES_DEFAULT: SuitPriceRule[] = [
  {
    id: "kids_3",
    label: "Детские костюмы 3-ки",
    priceRub: 14700,
    active: true,
    match: { categoryIncludes: ["детск"], pieces: 3 },
  },
  {
    id: "new_set",
    label: "Костюмы New (рубашка с карманами на груди + брюки)",
    priceRub: 14700,
    active: true,
    match: { categoryIncludes: ["new"] },
  },
  {
    id: "safari_3",
    label: "Костюмы сафари 3-ки",
    priceRub: 19700,
    active: true,
    match: { categoryIncludes: ["сафари"], pieces: 3 },
  },
  {
    id: "safari_2",
    label: "Костюмы сафари 2-ки",
    priceRub: 18600,
    active: true,
    match: { categoryIncludes: ["сафари"], pieces: 2 },
  },
  {
    id: "linen_3",
    label: "Костюмы льняные 3-ки",
    priceRub: 24800,
    active: true,
    match: { categoryIncludes: ["лен", "льнян"], pieces: 3 },
  },
  {
    id: "linen_2",
    label: "Костюмы льняные 2-ки",
    priceRub: 23700,
    active: true,
    match: { categoryIncludes: ["лен", "льнян"], pieces: 2 },
  },
  {
    id: "oversize",
    label: "Костюмы оверсайз",
    priceRub: 24700,
    active: true,
    match: { categoryIncludes: ["оверсайз"] },
  },
  {
    id: "smoking_8_3",
    label: "Смокинг 3-ка, 8 ростовка",
    priceRub: 25800,
    active: true,
    match: { line: "smoking", height: "8", pieces: 3 },
  },
  {
    id: "smoking_8_2",
    label: "Смокинг 2-ка, 8 ростовка",
    priceRub: 24700,
    active: true,
    match: { line: "smoking", height: "8", pieces: 2 },
  },
  {
    id: "smoking_6_3",
    label: "Смокинг 3-ка (однобортный / двубортный + бархатные)",
    priceRub: 24800,
    active: true,
    match: { line: "smoking", pieces: 3 },
  },
  {
    id: "smoking_6_2",
    label: "Смокинг 2-ка (однобортный / двубортный + бархатные)",
    priceRub: 23700,
    active: true,
    match: { line: "smoking", pieces: 2 },
  },
  {
    id: "casual_3",
    label: "Костюмы кэжуал (с накладными карманами) 3-ки",
    priceRub: 23700,
    active: true,
    match: { jacketNameIncludes: ["кэжуал"], pieces: 3 },
  },
  {
    id: "casual_2",
    label: "Костюмы кэжуал (с накладными карманами) 2-ки",
    priceRub: 22600,
    active: true,
    match: { jacketNameIncludes: ["кэжуал"], pieces: 2 },
  },
  {
    id: "four_button_3",
    label: "Костюмы с 4 застёжками 3-ки",
    priceRub: 24800,
    active: true,
    match: { jacketNameIncludes: ["4 пуговиц", "4 застёжк", "4 застежк"], pieces: 3 },
  },
  {
    id: "four_button_2",
    label: "Костюмы с 4 застёжками 2-ки",
    priceRub: 23700,
    active: true,
    match: { jacketNameIncludes: ["4 пуговиц", "4 застёжк", "4 застежк"], pieces: 2 },
  },
  {
    id: "double_breasted",
    label: "Двубортные",
    priceRub: 23700,
    active: true,
    match: { jacketNameIncludes: ["двубортн"] },
  },
  {
    id: "height8_3",
    label: "Костюмы 8 ростовки 3-ки",
    priceRub: 24700,
    active: true,
    match: { height: "8", pieces: 3 },
  },
  {
    id: "height8_2",
    label: "Костюмы 8 ростовки 2-ки",
    priceRub: 23600,
    active: true,
    match: { height: "8", pieces: 2 },
  },
  {
    id: "regular_3",
    label: "Костюмы 3-ки",
    priceRub: 23700,
    active: true,
    match: { pieces: 3 },
  },
  {
    id: "regular_2",
    label: "Костюмы 2-ки (без жилетки)",
    priceRub: 22600,
    active: true,
    match: { pieces: 2 },
  },
];

function normalize(value: string | undefined | null): string {
  return (value ?? "").trim().toLowerCase();
}

/** Подбор цены костюма по матрице: первое совпадающее активное правило. */
export function suitPriceOf(
  input: SuitPriceMatchInput,
  rules: SuitPriceRule[] = SUIT_PRICE_RULES_DEFAULT
): { rule: SuitPriceRule; priceRub: number } | null {
  const jacketName = normalize(input.jacketName);
  const category = normalize(input.category);
  const height = (input.height ?? "").trim() || "6";
  for (const rule of rules) {
    if (!rule.active) continue;
    const m = rule.match;
    if (m.pieces != null && m.pieces !== input.pieces) continue;
    if (m.line != null && m.line !== input.line) continue;
    if (m.height != null && m.height !== height) continue;
    if (m.categoryIncludes && !m.categoryIncludes.some((t) => category.includes(normalize(t)))) continue;
    if (m.jacketNameIncludes && !m.jacketNameIncludes.some((t) => jacketName.includes(normalize(t)))) continue;
    return { rule, priceRub: rule.priceRub };
  }
  return null;
}

/**
 * Матрица с учётом правок владельца из «Настроек»: логика match — только из
 * кода, `priceRub`/`active` — из оверрайда по `id`, если он есть.
 */
export function effectiveSuitPriceRules(
  overrides: Record<string, { priceRub: number; active: boolean }> | undefined,
  rules: SuitPriceRule[] = SUIT_PRICE_RULES_DEFAULT
): SuitPriceRule[] {
  if (!overrides || Object.keys(overrides).length === 0) return rules;
  return rules.map((rule) => {
    const o = overrides[rule.id];
    if (!o) return rule;
    return { ...rule, priceRub: o.priceRub, active: o.active };
  });
}
