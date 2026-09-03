"""Сбор вариации по справочнику."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import BRAND_ALIASES, MODEL_ALIASES
from expand import PositionRow, _clean


def load_abbr_maps(spr_rows: List[List[Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """K/L производитель/сокр, M/N модель/сокр.

    Если у одного имени несколько разных сокр. — в map кладём пусто + помечаем
    ключом `__conflict__:<name>` (для блокеров).
    """
    brand_vals: Dict[str, set] = {}
    model_vals: Dict[str, set] = {}
    for r in spr_rows[1:]:
        r = list(r) + [""] * 14
        b, ba, m, ma = _clean(r[10]), _clean(r[11]), _clean(r[12]), _clean(r[13])
        if b and ba:
            brand_vals.setdefault(b.lower(), set()).add(ba)
        if m and ma:
            model_vals.setdefault(m.lower(), set()).add(ma)

    brand: Dict[str, str] = {}
    model: Dict[str, str] = {}
    for k, vals in brand_vals.items():
        if len(vals) == 1:
            brand[k] = next(iter(vals))
        else:
            brand[f"__conflict__:{k}"] = "|".join(sorted(vals))
            # не даём однозначный abbr
    for k, vals in model_vals.items():
        if len(vals) == 1:
            model[k] = next(iter(vals))
        else:
            model[f"__conflict__:{k}"] = "|".join(sorted(vals))
    return brand, model


def normalize_brand(brand: str) -> str:
    b = _clean(brand)
    return BRAND_ALIASES.get(b.lower(), b)


def normalize_model(model: str) -> str:
    m = _clean(model)
    return MODEL_ALIASES.get(m.lower(), m)


def resolve_abbr(value: str, mapping: Dict[str, str]) -> Optional[str]:
    if not value:
        return None
    return mapping.get(value.lower())


def apply_variation(
    positions: List[PositionRow],
    brand_map: Dict[str, str],
    model_map: Dict[str, str],
) -> Tuple[List[PositionRow], List[Dict[str, str]]]:
    ready: List[PositionRow] = []
    blockers: List[Dict[str, str]] = []
    for p in positions:
        brand = normalize_brand(p.brand)
        model = normalize_model(p.model)
        # пустой производитель допустим → вариация без префикса бренда
        ba = resolve_abbr(brand, brand_map) if brand else ""
        ma = resolve_abbr(model, model_map)
        p.brand = brand
        p.model = model
        brand_conflict = brand_map.get(f"__conflict__:{brand.lower()}") if brand else None
        model_conflict = model_map.get(f"__conflict__:{model.lower()}") if model else None
        if brand_conflict:
            blockers.append(
                {
                    "reason": "ambiguous_brand_abbr",
                    "num": p.src_num,
                    "kit": p.src_kit,
                    "vid": p.vid,
                    "brand": brand,
                    "model": model,
                    "art": p.art,
                    "size": p.size,
                    "rost": p.rost,
                    "detail": f"несколько сокр. производителя: {brand_conflict}",
                }
            )
            p.status = "blocked"
            p.reason = "ambiguous_brand_abbr"
            continue
        if model_conflict:
            blockers.append(
                {
                    "reason": "ambiguous_model_abbr",
                    "num": p.src_num,
                    "kit": p.src_kit,
                    "vid": p.vid,
                    "brand": brand,
                    "model": model,
                    "art": p.art,
                    "size": p.size,
                    "rost": p.rost,
                    "detail": f"несколько сокр. модели: {model_conflict}",
                }
            )
            p.status = "blocked"
            p.reason = "ambiguous_model_abbr"
            continue
        if brand and not ba:
            blockers.append(
                {
                    "reason": "missing_brand_abbr",
                    "num": p.src_num,
                    "kit": p.src_kit,
                    "vid": p.vid,
                    "brand": brand,
                    "model": model,
                    "art": p.art,
                    "size": p.size,
                    "rost": p.rost,
                    "detail": f"нет сокр. производителя для {brand!r}",
                }
            )
            p.status = "blocked"
            p.reason = "missing_brand_abbr"
            continue
        if not ma:
            blockers.append(
                {
                    "reason": "missing_model_abbr",
                    "num": p.src_num,
                    "kit": p.src_kit,
                    "vid": p.vid,
                    "brand": brand,
                    "model": model,
                    "art": p.art,
                    "size": p.size,
                    "rost": p.rost,
                    "detail": f"нет сокр. модели для {model!r}",
                }
            )
            p.status = "blocked"
            p.reason = "missing_model_abbr"
            continue
        if not p.art:
            blockers.append(
                {
                    "reason": "missing_article",
                    "num": p.src_num,
                    "kit": p.src_kit,
                    "vid": p.vid,
                    "brand": brand,
                    "model": model,
                    "art": "",
                    "size": p.size,
                    "rost": p.rost,
                    "detail": "пустой артикул",
                }
            )
            p.status = "blocked"
            p.reason = "missing_article"
            continue
        p.brand_abbr = ba or ""
        p.model_abbr = ma
        # бренд пуст → только артикул + сокр.модели
        # если сокр. модели — только цифры, отделяем дефисом: AR70/710-1186
        sep = "-" if ma.isdigit() else ""
        p.variation = f"{ba}{p.art}{sep}{ma}"
        ready.append(p)
    return ready, blockers
