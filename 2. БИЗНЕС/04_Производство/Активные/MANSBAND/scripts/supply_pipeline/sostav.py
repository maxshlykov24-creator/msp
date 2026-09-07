"""Нормализация и формат состава для поля Описание в МС."""
from __future__ import annotations

import re
from typing import List, Tuple


_FIBER_FIX = {
    "sp": "эластан",
    "elastane": "эластан",
    "elastan": "эластан",
    "polyester": "полиэстер",
    "viscose": "вискоза",
    "wool": "шерсть",
    "cotton": "хлопок",
    "linen": "лён",
    "silk": "шёлк",
}


def _fix_fiber(name: str) -> str:
    raw = (name or "").replace("\xa0", " ").strip().strip(",")
    n = raw.lower()
    return _FIBER_FIX.get(n, raw)


def normalize_sostav(sostav: str) -> str:
    """
    Вход (сырьё):
      '60 шерсть 38 вискоза 2 эластан'
      '60% шерсть, 38% вискоза, 2% эластан'
    Выход (одна строка, компоненты через ', '):
      '60% шерсть, 38% вискоза, 2% эластан'
    Сортировка по % убыв.
    """
    s = (sostav or "").replace("\xa0", " ").strip()
    if not s:
        return ""
    # уже с запятыми
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        parsed: List[Tuple[float, str]] = []
        for p in parts:
            m = re.match(r"^(\d+(?:[.,]\d+)?)\s*%?\s+(.+)$", p)
            if not m:
                return s  # не трогаем нестандарт
            pct = float(m.group(1).replace(",", "."))
            fiber = _fix_fiber(m.group(2))
            parsed.append((pct, fiber))
        parsed.sort(key=lambda x: (-x[0], x[1]))
        return ", ".join(
            f"{int(p) if p == int(p) else p}% {f}" for p, f in parsed
        )

    # без запятых: «60 шерсть 38 вискоза 2 эластан»
    tokens = re.findall(
        r"(\d+(?:[.,]\d+)?)\s*%?\s+([^\d,%]+?)(?=\s+\d|\s*$)",
        s,
    )
    if not tokens:
        return s
    parsed = []
    for pct_s, fiber in tokens:
        pct = float(pct_s.replace(",", "."))
        parsed.append((pct, _fix_fiber(fiber)))
    parsed.sort(key=lambda x: (-x[0], x[1]))
    return ", ".join(f"{int(p) if p == int(p) else p}% {f}" for p, f in parsed)


def format_sostav_description(sostav: str) -> str:
    """
    В МС Описание = состав, каждый компонент с новой строки:

    70% шерсть,
    20% вискоза,
    10% полиэстер
    """
    s = normalize_sostav(sostav)
    if not s:
        return ""
    parts = [p.strip().rstrip(",") for p in re.split(r"[\n,]+", s) if p.strip()]
    if not parts:
        return s
    if len(parts) == 1:
        return parts[0]
    lines = [f"{p}," for p in parts[:-1]]
    lines.append(parts[-1])
    return "\n".join(lines)
