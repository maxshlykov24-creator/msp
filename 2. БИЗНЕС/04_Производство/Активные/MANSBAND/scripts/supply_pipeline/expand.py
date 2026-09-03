"""Разворот комплектов и сбор строк позиций."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from config import KIT_ALIASES, KIT_MAP


@dataclass
class PositionRow:
    src_num: str
    src_kit: str
    vid: str
    parent_uuid: str
    brand: str
    model: str
    rost: str
    art: str
    sostav: str
    color: str
    uzor: str
    kroy: str
    size: str
    qty: int
    brand_abbr: str = ""
    model_abbr: str = ""
    variation: str = ""
    status: str = ""
    reason: str = ""
    uuid: str = ""
    code: str = ""
    nomen_color: str = ""
    nomen_uzor: str = ""


def _clean(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\xa0", " ").strip()


def resolve_kit(kit: str) -> str:
    k = _clean(kit)
    if k in KIT_MAP:
        return k
    return KIT_ALIASES.get(k.lower(), k)


def uzor_for_vid(vid: str, default_uzor: str, note: str) -> str:
    """
    Косяк №1 и аналоги: в Коробке помечено, что у частей комплекта разный узор.
    Пример: «Жилет и брюки текстурный узор, пиджак - принц уэльский»
    Live МС: вариация общая, узор разный по виду.
    """
    n = _clean(note).lower()
    if not n:
        return default_uzor
    if "текстурн" in n and "принц" in n:
        if vid.startswith("Пиджак"):
            return "Принц Уэльский"
        if vid.startswith("Брюки") or vid.startswith("Жилет"):
            return "Текстурный"
    return default_uzor


def _qty(v: Any) -> Optional[int]:
    s = _clean(v)
    if not s:
        return None
    try:
        n = float(s.replace(",", "."))
    except ValueError:
        return None
    if n <= 0:
        return None
    return int(n) if float(n).is_integer() else int(n)


def load_parent_uuids(spr_rows: List[List[Any]]) -> Dict[str, str]:
    """Справочник: колонки Q/R (индекс 16/17) — Наименование / UUID."""
    out: Dict[str, str] = {}
    for r in spr_rows[1:]:
        r = list(r) + [""] * 18
        name, uid = _clean(r[16]), _clean(r[17])
        if name and uid:
            out[name] = uid
            out[name.lower()] = uid
    return out


def expand_flat(
    flat_rows: List[List[Any]],
    parent_uuids: Dict[str, str],
) -> Tuple[List[PositionRow], List[Dict[str, str]]]:
    """flat A..L → позиции. Возвращает (позиции, блокеры разворота)."""
    positions: List[PositionRow] = []
    blockers: List[Dict[str, str]] = []

    for i, raw in enumerate(flat_rows[1:], start=2):
        r = list(raw) + [""] * 14
        num = _clean(r[0])
        kit = _clean(r[1])
        brand = _clean(r[2])
        model = _clean(r[3])
        rost = _clean(r[4])
        art = _clean(r[5])
        sostav = _clean(r[6])
        color = _clean(r[7])
        uzor = _clean(r[8])
        kroy = _clean(r[9])
        size = _clean(r[10])
        qty = _qty(r[11])
        note = _clean(r[12]) if len(r) > 12 else ""
        if qty is None:
            blockers.append(
                {
                    "reason": "bad_qty",
                    "row": str(i),
                    "num": num,
                    "kit": kit,
                    "detail": f"qty={r[11]!r}",
                }
            )
            continue
        kit_canon = resolve_kit(kit)
        parts = KIT_MAP.get(kit_canon)
        if not parts:
            blockers.append(
                {
                    "reason": "unknown_kit",
                    "row": str(i),
                    "num": num,
                    "kit": kit,
                    "detail": "нет маппинга двойка/тройка",
                }
            )
            continue
        for vid in parts:
            uid = parent_uuids.get(vid) or parent_uuids.get(vid.lower(), "")
            if not uid:
                blockers.append(
                    {
                        "reason": "missing_parent_uuid",
                        "row": str(i),
                        "num": num,
                        "kit": kit,
                        "detail": vid,
                    }
                )
                continue
            positions.append(
                PositionRow(
                    src_num=num,
                    src_kit=kit_canon,
                    vid=vid,
                    parent_uuid=uid,
                    brand=brand,
                    model=model,
                    rost=rost,
                    art=art,
                    sostav=sostav,
                    color=color,
                    uzor=uzor_for_vid(vid, uzor, note),
                    kroy=kroy,
                    size=size,
                    qty=qty,
                )
            )
    return positions, blockers


def aggregate(positions: List[PositionRow]) -> List[PositionRow]:
    """Суммирует qty по антиключу после заполнения variation."""
    buckets: Dict[Tuple[str, str, str, str], PositionRow] = {}
    order: List[Tuple[str, str, str, str]] = []
    for p in positions:
        key = (p.vid, p.variation, p.size, p.rost)
        if key not in buckets:
            buckets[key] = p
            order.append(key)
        else:
            buckets[key].qty += p.qty
    return [buckets[k] for k in order]


def qty_control(flat_rows: List[List[Any]], positions: List[PositionRow]) -> Dict[str, Any]:
    """
    Строгий контроль:
      src_qty = сумма шт в flat
      expected_qty = сумма (шт × частей комплекта) по KIT_MAP
      expanded_qty = сумма qty после разворота/агрегации
    ok только если expanded_qty == expected_qty.
    Факт по размерам важнее ячейки «Всего» в сырье (пример: №7 = 45, не 40).
    """
    src = 0
    expected = 0
    for r in flat_rows[1:]:
        r = list(r) + [""] * 12
        q = _qty(r[11])
        if not q:
            continue
        src += q
        kit = resolve_kit(_clean(r[1]))
        parts = KIT_MAP.get(kit) or []
        expected += q * len(parts)
    out = sum(p.qty for p in positions)
    return {
        "src_qty": src,
        "expected_qty": expected,
        "expanded_qty": out,
        "ok": out == expected,
    }
