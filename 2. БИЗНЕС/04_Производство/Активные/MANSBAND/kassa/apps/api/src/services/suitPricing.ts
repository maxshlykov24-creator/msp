import { effectiveSuitPriceRules, suitPriceOf, suitTitle } from "@kassa/shared";
import type { SuitPart } from "@kassa/shared";
import * as catalog from "./catalog.js";
import { getAppSettings } from "./settings.js";

/**
 * Склейка отсканированных частей в костюм по цене матрицы (созвон 09.09):
 * пиджак + брюки одной вариации, размеры могут расходиться, жилет опционален.
 * В МойСклад всё равно уходят три изделия — эта склейка только считает, какую
 * цену матрицы применить и как её разнести по частям пропорционально их
 * цене в МойСклад (последняя часть добирает остаток округления).
 *
 * Одной вариации в чеке может быть несколько костюмов (46 и 48 — две строки).
 * Каждая строка целиком уходит в один костюм: у карточки одна цена, поэтому
 * количество пиджака и брюк в паре должно совпадать. Сначала пара одного
 * размера, потом оставшиеся, даже если размеры разные. Лишнее (пиджаков 2,
 * брюк 1) остаётся отдельной строкой, без второй цены на ту же позицию.
 */

export interface SuitPriceGroupPart {
  productId: string;
  part: SuitPart;
  size: string;
  /** Ростовка. Вместе с размером: «46/6». Пусто, если в карточке её нет. */
  height: string;
  qty: number;
  unitMsPriceRub: number;
  distributedUnitPriceRub: number;
}

export interface SuitPriceGroup {
  groupId: string;
  variation: string;
  title: string;
  hasVest: boolean;
  qty: number;
  /** Цена костюма из матрицы за штуку, ₽. null — правило не нашлось. */
  matrixUnitPriceRub: number | null;
  matchedRuleLabel: string | null;
  parts: SuitPriceGroupPart[];
}

export interface SuitPriceGroupResult {
  groups: SuitPriceGroup[];
  /** Позиции, которые не вошли ни в одну группу (штучные, разнобой количеств). */
  unmatchedProductIds: string[];
}

interface Line {
  productId: string;
  qty: number;
}

interface Pool {
  productId: string;
  size: string;
  qty: number;
  priceRub: number;
}

/** Пропорциональное разнесение цены костюма по частям (приём `normalizePayouts`). */
function pullPool(list: Pool[], item: Pool): void {
  const index = list.findIndex((p) => p.productId === item.productId);
  if (index >= 0) list.splice(index, 1);
}

/** Сначала пара того же размера и того же количества, иначе любая с тем же количеством. */
function takePair(jackets: Pool[], trousers: Pool[]): { jacket: Pool; trousers: Pool } | null {
  for (const jacket of jackets) {
    const same = trousers.find((t) => t.qty === jacket.qty && t.size.trim() === jacket.size.trim());
    if (same) return { jacket, trousers: same };
  }
  for (const jacket of jackets) {
    const any = trousers.find((t) => t.qty === jacket.qty);
    if (any) return { jacket, trousers: any };
  }
  return null;
}

function takeVest(vests: Pool[], jacket: Pool): Pool | null {
  return (
    vests.find((v) => v.qty === jacket.qty && v.size.trim() === jacket.size.trim()) ??
    vests.find((v) => v.qty === jacket.qty) ??
    null
  );
}

function distribute(totalRub: number, parts: Pool[]): number[] {
  const basis = parts.reduce((s, p) => s + p.priceRub, 0);
  if (parts.length === 0) return [];
  if (basis <= 0) return parts.map(() => Math.round(totalRub / parts.length));
  const shares: number[] = [];
  let used = 0;
  for (let i = 0; i < parts.length - 1; i += 1) {
    const share = Math.round((totalRub * parts[i]!.priceRub) / basis);
    shares.push(share);
    used += share;
  }
  shares.push(Math.max(0, totalRub - used));
  return shares;
}

export async function priceGroupSuits(lines: Line[]): Promise<SuitPriceGroupResult> {
  const valid = lines.filter((l) => l.productId?.trim() && l.qty > 0);
  const unmatchedProductIds: string[] = [];
  if (valid.length === 0) return { groups: [], unmatchedProductIds };

  const info = await catalog.suitPricingInfoByMsIds(valid.map((l) => l.productId));
  const settings = await getAppSettings();
  const rules = effectiveSuitPriceRules(settings.suitPriceOverrides);

  const byVariation = new Map<string, { jacket: Pool[]; trousers: Pool[]; vest: Pool[] }>();
  for (const line of valid) {
    const suit = info.get(line.productId);
    if (!suit) {
      unmatchedProductIds.push(line.productId);
      continue;
    }
    const bucket = byVariation.get(suit.variation) ?? { jacket: [], trousers: [], vest: [] };
    bucket[suit.part].push({
      productId: line.productId,
      size: suit.size,
      qty: line.qty,
      priceRub: suit.priceRub,
    });
    byVariation.set(suit.variation, bucket);
  }

  const groups: SuitPriceGroup[] = [];
  for (const [variation, pools] of byVariation) {
    const jackets = [...pools.jacket];
    const trousers = [...pools.trousers];
    const vests = [...pools.vest];

    for (;;) {
      const pair = takePair(jackets, trousers);
      if (!pair) break;
      pullPool(jackets, pair.jacket);
      pullPool(trousers, pair.trousers);
      const vest = takeVest(vests, pair.jacket);
      if (vest) pullPool(vests, vest);

      const jacket = pair.jacket;
      const qty = jacket.qty;
      const hasVest = vest != null;
      const jacketInfo = info.get(jacket.productId)!;
      const matched = suitPriceOf(
        {
          jacketName: jacketInfo.name,
          category: jacketInfo.category ?? "",
          pieces: hasVest ? 3 : 2,
          line: jacketInfo.line,
          height: jacketInfo.height,
        },
        rules
      );

      const basisParts: Pool[] = vest ? [jacket, pair.trousers, vest] : [jacket, pair.trousers];
      const shares = matched
        ? distribute(matched.priceRub, basisParts)
        : basisParts.map((p) => p.priceRub);

      const parts: SuitPriceGroupPart[] = basisParts.map((p, i) => ({
        productId: p.productId,
        part: p === jacket ? "jacket" : p === pair.trousers ? "trousers" : "vest",
        size: p.size,
        height: info.get(p.productId)?.height ?? "",
        qty,
        unitMsPriceRub: p.priceRub,
        distributedUnitPriceRub: shares[i]!,
      }));

      groups.push({
        groupId: `${variation}|${hasVest ? 3 : 2}|${jacket.productId}|${pair.trousers.productId}`,
        variation,
        title: suitTitle({
          hasVest,
          line: jacketInfo.line,
          color: jacketInfo.color,
          pattern: jacketInfo.pattern,
          fit: jacketInfo.fit,
        }),
        hasVest,
        qty,
        matrixUnitPriceRub: matched ? matched.priceRub : null,
        matchedRuleLabel: matched ? matched.rule.label : null,
        parts,
      });
    }

    for (const p of [...jackets, ...trousers, ...vests]) unmatchedProductIds.push(p.productId);
  }

  return { groups, unmatchedProductIds };
}
