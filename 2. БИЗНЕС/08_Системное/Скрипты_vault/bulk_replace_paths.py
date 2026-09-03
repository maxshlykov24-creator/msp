#!/usr/bin/env python3
"""Замена путей vault: корень — каталог «Личное». Актуально под 3. Разум: «3.1. Изучение» … «3.9. Вопросы» (сплошной ряд после сжатия нумерации 2026-03). Ниже — устаревшие соответствия для разовых миграций с очень старых имён."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKIP_DIRS = {".git", "node_modules", ".obsidian"}

sys.path.insert(0, str(ROOT / "Распределение"))
from rename_profile_numbered import rel_map_profile

# Устаревшие переименования (3.0.n → старая схема с дырами 3.2/3.5 и с 3.10–3.11); на диске сейчас: 3.2. Кино … 3.9. Вопросы
RAZUM_3DOT_TO_DD_DOT = [
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

RAZUM_OLD_NEW = [
    ("0.10 Хотелки", "3.10. Хотелки"),
    ("0.9 Финансы", "3.9. Финансы"),
    ("0.8 Семья", "3.8. Семья"),
    ("0.7 Рестораны", "3.7. Рестораны"),
    ("0.6 Путешествия", "3.6. Путешествия"),
    ("0.5 Музыка", "3.5. Музыка"),
    ("0.4 Люди", "3.4. Люди"),
    ("0.3 Кино", "3.3. Кино"),
    ("0.2 Инсайты", "3.2. Инсайты"),
    ("0.1 Изучение", "3.1. Изучение"),
]


def pairs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    # 2026-07-07: нумерация корня vault + 3. АГЕНТЫ
    out.append(("Входящее/", "_ВХОДЯЩЕЕ/"))
    out.append(("Личное/", "1. ЛИЧНОЕ/"))
    out.append(("Бизнес/агенты/", "__BIZ_AGENTS__"))
    out.append(("Бизнес/", "2. БИЗНЕС/"))
    out.append(("__BIZ_AGENTS__", "2. БИЗНЕС/агенты/"))
    out.append(("агенты/", "3. АГЕНТЫ/"))

    # 2026-07-08: старые имена зон внутри бизнес-дерева
    out.append(("04_Производство_внедрений/", "04_Производство/"))
    out.append(("04_Производство_внедрений", "04_Производство"))
    out.append(("99_Системное/", "08_Системное/"))
    out.append(("99_Системное", "08_Системное"))
    out.append(("01_Стратегия_и_управление/", "01_Стратегия/"))
    out.append(("02_Финансы_и_риски/", "02_Финансы/"))
    out.append(("06_Продукт_и_упаковка/", "07_Продукт/"))
    out.append(("08_Поддержка/", "05_Поддержка/"))

    # 2026-07-07: убран промежуточный уровень 1. Профиль
    out.append(("1. ЛИЧНОЕ/1. Профиль/", "1. ЛИЧНОЕ/"))
    out.append(("1. ЛИЧНОЕ/1. Профиль", "1. ЛИЧНОЕ"))

    for old_s, new_s in RAZUM_3DOT_TO_DD_DOT:
        oe, ne = old_s.replace(" ", "%20"), new_s.replace(" ", "%20")
        out.append((f"1. ЛИЧНОЕ/3. Разум/{old_s}", f"1. ЛИЧНОЕ/3. Разум/{new_s}"))
        out.append((f"3. Разум/{old_s}", f"3. Разум/{new_s}"))
        out.append((f"3. Разум/{old_s}", f"3. Разум/{new_s}"))
        out.append((f"3.%20Разум/{oe}", f"3.%20Разум/{ne}"))

    for old_s, new_s in RAZUM_OLD_NEW:
        oe, ne = old_s.replace(" ", "%20"), new_s.replace(" ", "%20")
        out.append((f"1. ЛИЧНОЕ/2. Профиль/3. Разум/{old_s}", f"1. ЛИЧНОЕ/3. Разум/{new_s}"))
        out.append((f"2. Профиль/3. Разум/{old_s}", f"3. Разум/{new_s}"))
        out.append((f"3. Разум/{old_s}", f"3. Разум/{new_s}"))
        out.append((f"3.%20Разум/{oe}", f"3.%20Разум/{ne}"))

    out.append(("1. ЛИЧНОЕ/2. Профиль", "1. ЛИЧНОЕ/1. Профиль"))
    out.append(("1. ЛИЧНОЕ/3. Тесты", "1. ЛИЧНОЕ/2. Тесты"))
    out.append(("1. ЛИЧНОЕ/1. Входящее", "1. ЛИЧНОЕ/_ВХОДЯЩЕЕ"))

    m = rel_map_profile()
    skip_singleton = {"Отчёты", "Дух", "Разум", "Тело", "Документы", "Прочее"}
    out.extend([(k, v) for k, v in m.items() if "/" in k or k not in skip_singleton])

    out.append(("1. ЛИЧНОЕ/00_Входящее", "1. ЛИЧНОЕ/_ВХОДЯЩЕЕ"))
    out.append(("1. ЛИЧНОЕ/01_Профиль", "1. ЛИЧНОЕ/1. Профиль"))
    out.append(("1. ЛИЧНОЕ/02_Тесты", "1. ЛИЧНОЕ/2. Тесты"))
    out.append(("01_Профиль/", "1. Профиль/"))
    out.append(("01_Профиль", "1. Профиль"))
    out.append(("00_Входящее", "_ВХОДЯЩЕЕ"))
    out.append(("02_Тесты", "2. Тесты"))
    # Не подставляем «Дух/»→«2. Дух/» и т.п.: в уже нумерованных путях получится «2. 2. Дух».

    out.sort(key=lambda x: (-len(x[0]), x[0]))
    return out


def dedupe_zone_prefixes(s: str) -> str:
    """Убирает двойные префиксы зон после ошибочной подстановки (2. 2. Дух → 2. Дух)."""
    pairs = [
        ("7. 6. 6. Прочее", "7. Прочее"),
        ("7. 6. Прочее", "7. Прочее"),
        ("3. 3. Разум", "3. Разум"),
        ("2. 2. Дух", "2. Дух"),
        ("1. 1. Отчёты", "1. Отчёты"),
        ("4. 4. Тело", "4. Тело"),
        ("5. 5. Документы", "5. Документы"),
        ("6. 6. Прочее", "6. Прочее"),
    ]
    for _ in range(6):
        prev = s
        for a, b in pairs:
            s = s.replace(a, b)
        if s == prev:
            break
    return s


EXT = {".md", ".html", ".cursorrules", ".json", ".txt"}


def should_skip(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS)


def patch_text(s: str) -> str:
    for old, new in pairs():
        if old in s:
            s = s.replace(old, new)

    s = s.replace("../01_Профиль/", "../1. Профиль/")
    s = s.replace("../00_Входящее", "../_ВХОДЯЩЕЕ")
    s = s.replace("../2. Профиль/", "../1. Профиль/")
    s = s.replace("../1. Входящее", "../_ВХОДЯЩЕЕ")
    s = re.sub(r"`01_Профиль`", "`1. Профиль`", s)
    s = re.sub(r"# 01_Профиль\b", "# 1. Профиль", s)
    s = re.sub(r"`1. ЛИЧНОЕ/01_Профиль`", "`1. ЛИЧНОЕ/1. Профиль`", s)

    # Оставшиеся вхождения «корень личного» без префикса 1. ЛИЧНОЕ/
    s = re.sub(r"(?<!\d)1\. Входящее\b", "_ВХОДЯЩЕЕ", s)
    s = re.sub(r"(?<!\d)2\. Профиль\b", "1. Профиль", s)
    s = re.sub(r"(?<!\d)3\. Тесты\b", "2. Тесты", s)
    s = dedupe_zone_prefixes(s)
    return s


def main():
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        if should_skip(path):
            continue
        if path.suffix.lower() not in EXT and path.name not in (".cursorrules",):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_text = patch_text(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
