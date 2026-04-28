from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.tier_manager import TIER_RATES


def _folder_id_from_assortment(assortment: dict[str, Any]) -> str | None:
    if not assortment:
        return None
    pf = assortment.get("productFolder")
    if isinstance(pf, dict):
        mid = pf.get("id")
        if mid:
            return str(mid)
    return None


def _is_in_folders(assortment: dict[str, Any], folder_ids: frozenset[str]) -> bool:
    if not folder_ids:
        return False
    fid = _folder_id_from_assortment(assortment)
    return fid is not None and fid in folder_ids


def _line_amount_rub(pos: dict[str, Any]) -> int:
    """Сумма строки в рублях (округление вниз после перевода из копеек)."""
    price = pos.get("price")
    qty = float(pos.get("quantity") or 0)
    if price is None:
        return 0
    price_f = float(price)
    # В МойСклад цена позиции обычно в копейках
    kop = int(round(price_f * qty))
    return kop // 100


def _line_discount_percent(pos: dict[str, Any]) -> float:
    d = pos.get("discount")
    if d is None:
        return 0.0
    try:
        return float(d)
    except (TypeError, ValueError):
        return 0.0


def extract_bonus_paid_rub(order: dict[str, Any]) -> int:
    """
    Сумма оплаты бонусами в рублях (если поле есть в API).
    Названия полей могут отличаться по версии — при необходимости расширить после теста на реальном заказе.
    """
    for key in ("payedBonus", "payedInBonuses", "paidByBonus", "bonusPayedSum"):
        v = order.get(key)
        if v is None:
            continue
        try:
            kop = int(float(v))
            return max(0, kop // 100)
        except (TypeError, ValueError):
            continue
    return 0


@dataclass
class CashbackResult:
    total_bonus_points: int
    tier_used: int
    provisional_annual_rub: int
    details: dict[str, Any]


def compute_cashback_for_order(
    *,
    order: dict[str, Any],
    settings: Settings,
    current_annual_rub: int,
    current_tier_locked: bool,
    current_tier_index: int,
) -> CashbackResult:
    pos_block = order.get("positions") or {}
    if isinstance(pos_block, dict):
        positions = list(pos_block.get("rows") or [])
    elif isinstance(pos_block, list):
        positions = pos_block
    else:
        positions = []
    outlet = settings.outlet_folder_ids_set
    gift = settings.gift_folder_ids_set
    delivery = settings.delivery_folder_ids_set

    eligible_lines: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for row in positions:
        pos = row if isinstance(row, dict) else {}
        assortment = pos.get("assortment") or {}
        if not assortment:
            continue
        if _is_in_folders(assortment, gift):
            continue
        if _is_in_folders(assortment, delivery):
            continue
        if _is_in_folders(assortment, outlet):
            # аутлет: начисления нет — пропускаем строку полностью
            continue
        rub = _line_amount_rub(pos)
        if rub <= 0:
            continue
        eligible_lines.append((rub, pos, assortment))

    eligible_total_rub = sum(x[0] for x in eligible_lines)
    bonus_paid_rub = extract_bonus_paid_rub(order)

    scale = 1.0
    if eligible_total_rub > 0 and bonus_paid_rub > 0:
        # пропорционально уменьшаем базу под кэшбэк: бонусная оплата не даёт кэшбэк и не в годовую сумму
        scale = max(0.0, (eligible_total_rub - min(bonus_paid_rub, eligible_total_rub)) / eligible_total_rub)

    cashable_total_rub = int(math.floor(eligible_total_rub * scale + 1e-9))
    provisional_annual = current_annual_rub + cashable_total_rub

    from app.tier_manager import tier_index_from_annual_sum

    tier_for_calc = current_tier_index
    if not current_tier_locked:
        tier_for_calc = tier_index_from_annual_sum(provisional_annual)

    full_pct, disc_pct = TIER_RATES[tier_for_calc]

    total_bonus = 0
    line_details: list[dict[str, Any]] = []
    for rub, pos, _ass in eligible_lines:
        cashable_rub = int(math.floor(rub * scale + 1e-9))
        if cashable_rub <= 0:
            continue
        dp = _line_discount_percent(pos)
        pct = disc_pct if dp > 0 else full_pct
        # скидка >= 51%: начисление по общим правилам (уже учтено через pct), списание на кассе — вне сервиса
        line_bonus = math.ceil(cashable_rub * pct / 100)
        total_bonus += line_bonus
        line_details.append(
            {
                "rub": rub,
                "cashable_rub": cashable_rub,
                "discount_percent": dp,
                "percent_used": pct,
                "line_bonus": line_bonus,
            }
        )

    details = {
        "eligible_total_rub": eligible_total_rub,
        "bonus_paid_rub": bonus_paid_rub,
        "scale": scale,
        "cashable_total_rub": cashable_total_rub,
        "provisional_annual_rub": provisional_annual,
        "tier_used": tier_for_calc,
        "lines": line_details,
    }
    return CashbackResult(
        total_bonus_points=total_bonus,
        tier_used=tier_for_calc,
        provisional_annual_rub=provisional_annual,
        details=details,
    )
