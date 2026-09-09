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
 * Группа образуется только если пиджак и брюки этой вариации в чеке — РОВНО
 * одна позиция каждый и их количество совпадает целиком. Частичное совпадение
 * (например, пиджаков 2, брюк 1) не группируем: у карточки чека одна цена на
 * всю строку, а разное количество означало бы две разные цены для одной
 * позиции — такие случаи остаются обычными строками по цене МойСклад, консультанту
 * уже показывает предупреждение `SuitBreakWarning` про образующийся полупарк.
 */

export interface SuitPriceGroupPart {
  productId: string;
  part: SuitPart;
  size: string;
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
    const jacket = pools.jacket.length === 1 ? pools.jacket[0]! : null;
    const trousers = pools.trousers.length === 1 ? pools.trousers[0]! : null;
    const ambiguousParts = pools.jacket.length > 1 || pools.trousers.length > 1;
    if (!jacket || !trousers || ambiguousParts || jacket.qty !== trousers.qty) {
      for (const p of [...pools.jacket, ...pools.trousers, ...pools.vest]) {
        unmatchedProductIds.push(p.productId);
      }
      continue;
    }

    const qty = jacket.qty;
    let vest: Pool | null = null;
    if (pools.vest.length === 1 && pools.vest[0]!.qty === qty) {
      vest = pools.vest[0]!;
    } else if (pools.vest.length > 0) {
      // Жилет есть, но количество не совпадает целиком (или больше одной позиции
      // жилета) — не группируем совсем, чтобы не оставить его необъяснимым
      // полупарком без разнесённой цены.
      for (const p of [jacket, trousers, ...pools.vest]) unmatchedProductIds.push(p.productId);
      continue;
    }
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

    const basisParts: Pool[] = vest ? [jacket, trousers, vest] : [jacket, trousers];
    const shares = matched
      ? distribute(matched.priceRub, basisParts)
      : basisParts.map((p) => p.priceRub);

    const parts: SuitPriceGroupPart[] = basisParts.map((p, i) => ({
      productId: p.productId,
      part: p === jacket ? "jacket" : p === trousers ? "trousers" : "vest",
      size: p.size,
      qty,
      unitMsPriceRub: p.priceRub,
      distributedUnitPriceRub: shares[i]!,
    }));

    groups.push({
      groupId: `${variation}|${hasVest ? 3 : 2}|${jacket.productId}|${trousers.productId}`,
      variation,
      title: suitTitle({ hasVest, line: jacketInfo.line, color: null, pattern: null, fit: null }),
      hasVest,
      qty,
      matrixUnitPriceRub: matched ? matched.priceRub : null,
      matchedRuleLabel: matched ? matched.rule.label : null,
      parts,
    });
  }

  return { groups, unmatchedProductIds };
}
