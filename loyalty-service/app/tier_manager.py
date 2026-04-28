from __future__ import annotations

# (полный %, со скидкой %) по уровню
TIER_RATES = (
    (5, 3),
    (7, 5),
    (10, 7),
)


def tier_index_from_annual_sum(annual_rub: int) -> int:
    if annual_rub < 20_000:
        return 0
    if annual_rub < 60_000:
        return 1
    return 2


def tier_name_ru(idx: int) -> str:
    return ("Знакомство", "Дружба", "Любовь")[idx]
