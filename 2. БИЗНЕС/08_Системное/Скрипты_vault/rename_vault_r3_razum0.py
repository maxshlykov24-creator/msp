#!/usr/bin/env python3
"""Уже применено (2026-03): Личное: 1/2/3 → 0/1/2; дети 3. Разум: 0.n → 3.0.n.
Повторный запуск без старых имён папок завершится ошибкой FileNotFoundError.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path("/Users/max/Desktop/CURSOR")
LICHNOE = ROOT / "Личное"
PROFILE_OLD = LICHNOE / "2. Профиль"
RAZUM = PROFILE_OLD / "3. Разум"

RAZUM_CHILDREN = [
    "0.1 Изучение",
    "0.2 Инсайты",
    "0.3 Кино",
    "0.4 Люди",
    "0.5 Музыка",
    "0.6 Путешествия",
    "0.7 Рестораны",
    "0.8 Семья",
    "0.9 Финансы",
    "0.10 Хотелки",
]


def mv(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        raise FileExistsError(dst)
    src.rename(dst)
    print(f"OK {src.name} → {dst.name}")


def main() -> None:
    # Сначала дети Разума (пока путь ещё 2. Профиль)
    for name in sorted(RAZUM_CHILDREN, key=len, reverse=True):
        if not name.startswith("0."):
            continue
        rest = name.split(" ", 1)
        num = rest[0]
        tail = rest[1] if len(rest) > 1 else ""
        new_name = f"3.{num}" + (f" {tail}" if tail else "")
        mv(RAZUM / name, RAZUM / new_name)

    # Корень Личное: освобождаем номера по цепочке
    mv(LICHNOE / "1. Входящее", LICHNOE / "0. Входящее")
    mv(LICHNOE / "2. Профиль", LICHNOE / "1. Профиль")
    mv(LICHNOE / "3. Тесты", LICHNOE / "2. Тесты")


if __name__ == "__main__":
    main()
