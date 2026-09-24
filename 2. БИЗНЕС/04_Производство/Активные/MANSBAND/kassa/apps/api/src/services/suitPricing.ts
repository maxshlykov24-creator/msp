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
 * Сначала пара одного размера, потом оставшиеся, даже если размеры разные.
 * Количество может не совпадать: в костюм уходит меньшее (пиджак 1 и брюки 2
 * дают один костюм), остаток штук остаётся отдельной строкой.
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

/** Сначала пара того же размера, иначе любая. В костюм уходит меньшее количество. */
function takePair(jackets: Pool[], trousers: Pool[]): { jacket: Pool; trousers: Pool; qty: number } | null {
  const jacket =
    jackets.find((j) => trousers.some((t) => t.size.trim() === j.size.trim())) ?? jackets[0];
  if (!jacket) return null;
  const trousersLine =
    trousers.find((t) => t.size.trim() === jacket.size.trim()) ?? trousers[0];
  if (!trousersLine) return null;
  return { jacket, trousers: trousersLine, qty: Math.min(jacket.qty, trousersLine.qty) };
}

function takeVest(vests: Pool[], jacket: Pool): Pool | null {
  return vests.find((v) => v.size.trim() === jacket.size.trim()) ?? vests[0] ?? null;
}

/** Списывает штуки пары. Нулевой остаток убирает строку из пула. */
function consume(list: Pool[], item: Pool, qty: number): Pool {
  item.qty -= qty;
  if (item.qty <= 0) pullPool(list, item);
  return { ...item, qty };
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
      const qty = pair.qty;
      const jacket = consume(jackets, pair.jacket, qty);
      const trouser = consume(trousers, pair.trousers, qty);
      const vestPool = takeVest(vests, pair.jacket);
      const vest = vestPool && vestPool.qty >= qty ? consume(vests, vestPool, qty) : null;
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

      const basisParts: Pool[] = vest ? [jacket, trouser, vest] : [jacket, trouser];
      const shares = matched
        ? distribute(matched.priceRub, basisParts)
        : basisParts.map((p) => p.priceRub);

      const parts: SuitPriceGroupPart[] = basisParts.map((p, i) => ({
        productId: p.productId,
        part: p === jacket ? "jacket" : p === trouser ? "trousers" : "vest",
        size: p.size,
        height: info.get(p.productId)?.height ?? "",
        qty: p.qty,
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
