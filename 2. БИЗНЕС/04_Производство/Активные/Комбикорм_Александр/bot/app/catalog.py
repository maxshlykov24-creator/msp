"""Импорт номенклатуры из CSV МойСклад → products + aliases.

Фильтруем только активные розничные позиции (``Статус`` = да), строим короткие
различимые имена и наборы алиасов (бренд + код + вариант) для голосового матчинга.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .text.normalize import normalize

# ожидаемые заголовки CSV (МойСклад-выгрузка)
COL_NAME = "Наименование"
COL_GROUP = "Группа"
COL_LINE = "Линейка"
COL_PRICE_BAG = "ЦЕНА позиции"
COL_PRICE_KG = "ЦЕНА кг (из мешка)"
COL_SIZE = "Размер (кг)"
COL_STATUS = "Статус"
COL_COST = "CC"

# коды вида ПК-2, ПК-1-2, К-6 и т.п.
_CODE_RE = re.compile(r"\b([А-Яа-яA-Za-z]{1,4})[\s\-]?(\d+(?:[\s\-]\d+)*)\b")


def _num(raw: str) -> Optional[float]:
    if raw is None:
        return None
    raw = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        return None
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _is_active(raw: str) -> bool:
    return (raw or "").strip().lower() in {"да", "yes", "1", "true", "актив", "активен"}


def _extract_code(name: str) -> Optional[str]:
    m = _CODE_RE.search(name)
    if not m:
        return None
    letters = m.group(1)
    digits = re.sub(r"[\s\-]+", "-", m.group(2))
    return f"{letters}-{digits}".lower()


def _variant(name: str) -> Optional[str]:
    m = re.search(r"\(([^)]+)\)", name)
    return m.group(1).strip() if m else None


def build_aliases(name: str, brand: Optional[str], line: Optional[str], code: Optional[str]) -> set[str]:
    aliases: set[str] = set()
    n = normalize(name)
    aliases.add(n)
    if brand:
        b = normalize(brand)
        if b and b != "зерно":  # 'зерно' — слишком общая группа, не как бренд
            aliases.add(normalize(f"{brand} {name}"))
            aliases.add(normalize(f"{name} {brand}"))
            if code:
                aliases.add(normalize(f"{brand} {code}"))
    if code:
        aliases.add(normalize(code))
    if line:
        aliases.add(normalize(f"{name} {line}"))
    # выкидываем пустые
    return {a for a in aliases if a}


def parse_csv(csv_path: Path | str, *, only_active: bool = True) -> list[dict]:
    """Читает CSV и возвращает список продуктов с алиасами (розница, активные)."""
    products: list[dict] = []
    base_name_count: dict[str, int] = defaultdict(int)

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # первый проход — посчитать коллизии имён
    for row in rows:
        name = (row.get(COL_NAME) or "").strip()
        if not name:
            continue
        if only_active and not _is_active(row.get(COL_STATUS, "")):
            continue
        base_name_count[name] += 1

    seen: dict[str, int] = defaultdict(int)
    for row in rows:
        name = (row.get(COL_NAME) or "").strip()
        if not name:
            continue
        if only_active and not _is_active(row.get(COL_STATUS, "")):
            continue

        brand = (row.get(COL_GROUP) or "").strip() or None
        line = (row.get(COL_LINE) or "").strip() or None
        size = _num(row.get(COL_SIZE, ""))
        code = _extract_code(name)
        variant = _variant(name)

        canonical = name
        # при дубле имени различаем размером мешка
        if base_name_count[name] > 1:
            seen[name] += 1
            if size:
                canonical = f"{name} [{size:g} кг]"
            else:
                canonical = f"{name} #{seen[name]}"

        aliases = build_aliases(canonical, brand, line, code)

        products.append(
            {
                "canonical_name": canonical,
                "brand": brand,
                "code": code,
                "variant": variant,
                "line": line,
                "bag_size_kg": size,
                "price_bag": _num(row.get(COL_PRICE_BAG, "")),
                "price_kg": _num(row.get(COL_PRICE_KG, "")),
                "cost": _num(row.get(COL_COST, "")),
                "active": True,
                "aliases": sorted(aliases),
            }
        )
    return products
