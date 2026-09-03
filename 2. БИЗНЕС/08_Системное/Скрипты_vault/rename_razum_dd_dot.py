#!/usr/bin/env python3
"""Уже применено: «3.0.1 Изучение» → «3.1. Изучение», остальные «3.0.n» → «3.n.» … «3.10.». Повторно не гонять."""
from __future__ import annotations

from pathlib import Path

RAZUM = Path("/Users/max/Desktop/CURSOR/1. ЛИЧНОЕ/3. Разум")

MAP = [
    ("3.0.10 Хотелки", "3.10. Хотелки"),
    ("3.0.9 Финансы", "3.9. Финансы"),
    ("3.0.8 Семья", "3.8. Семья"),
    ("3.0.7 Рестораны", "3.7. Рестораны"),
    ("3.0.6 Путешествия", "3.6. Путешествия"),
    ("3.0.5 Музыка", "3.5. Музыка"),
    ("3.0.4 Люди", "3.4. Люди"),
    ("3.0.3 Кино", "3.3. Кино"),
    ("3.0.2 Инсайты", "3.2. Инсайты"),
    ("3.0.1 Изучение", "3.1. Изучение"),
]


def main() -> None:
    for old, new in MAP:
        src, dst = RAZUM / old, RAZUM / new
        if not src.is_dir():
            raise FileNotFoundError(src)
        if dst.exists():
            raise FileExistsError(dst)
        src.rename(dst)
        print(f"OK {old} → {new}")


if __name__ == "__main__":
    main()
