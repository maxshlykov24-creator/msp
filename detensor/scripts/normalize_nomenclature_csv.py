"""
Нормализует экспорт номенклатуры МойСклад в detensor/data/nomenclature_original.csv

Колонки на выходе: article, name_long

Запуск:
  python3 scripts/normalize_nomenclature_csv.py /path/to/Sheet0.csv
"""

import csv
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "nomenclature_original.csv"

ARTICLE_KEYS = ("article", "артикул", "код")
NAME_KEYS = ("name_long", "наименование", "название", "name")


def pick(row: dict, keys: tuple[str, ...]) -> str:
    lower = {k.strip().lower(): v for k, v in row.items() if k}
    for key in keys:
        if key in lower and lower[key] is not None:
            return str(lower[key]).strip()
    return ""


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/normalize_nomenclature_csv.py <source.csv>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"Файл не найден: {src}")
        sys.exit(1)

    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("Пустой CSV или нет заголовка")
            sys.exit(1)
        rows_out = []
        skipped = 0
        for row in reader:
            art = pick(row, ARTICLE_KEYS)
            name = pick(row, NAME_KEYS)
            if not art or not name:
                skipped += 1
                continue
            rows_out.append({"article": art, "name_long": name})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["article", "name_long"])
        w.writeheader()
        w.writerows(rows_out)

    print(f"Записано: {OUT} ({len(rows_out)} строк, пропущено {skipped})")


if __name__ == "__main__":
    main()
