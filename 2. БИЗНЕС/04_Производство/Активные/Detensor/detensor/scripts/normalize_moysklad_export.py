"""
Нормализует TSV/CSV экспорт номенклатуры МойСклад (admin export) в
detensor/data/nomenclature_original.csv

Колонки экспорта (таб):
  Группы | UUID | Тип | Код | Наименование | Внешний код | Артикул | Доп. поле: Артикул

name_long  ← «Наименование» (юридическое / исходное имя в MS)
article    ← последняя непустая колонка, если похожа на SKU;
             иначе «Доп. поле: Артикул» / «Артикул» из заголовка

Запуск:
  python3 scripts/normalize_moysklad_export.py data/nomenclature_export.tsv
  python3 scripts/normalize_moysklad_export.py data/nomenclature_export.tsv --only-rename-map
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from step3_rename import RENAME_MAP  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "nomenclature_original.csv"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-+_]{1,31}$")


def looks_like_uuid(s: str) -> bool:
    return bool(UUID_RE.match(s.strip()))


def looks_like_sku(s: str) -> bool:
    s = s.strip()
    if not s or " " in s:
        return False
    return bool(SKU_RE.match(s))


def parse_row(parts: list[str]) -> tuple[str, str] | None:
    """Вернуть (article, name_long) или None."""
    parts = [p.strip() for p in parts]
    if len(parts) < 4:
        return None

    if looks_like_uuid(parts[0]):
        # строка без «Группы» в начале
        name_long = parts[3] if len(parts) > 3 else ""
        tail = parts[4:]
    else:
        name_long = parts[4] if len(parts) > 4 else ""
        tail = parts[5:]

    if not name_long:
        return None

    article = ""
    for cell in reversed(tail):
        if looks_like_sku(cell):
            article = cell
            break
    if not article:
        return None
    return article, name_long


def read_rows(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    delim = "\t" if "\t" in text.splitlines()[0] else ","
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Группы"):
            continue
        parsed = parse_row(line.split(delim))
        if parsed:
            rows.append(parsed)
    return rows


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/normalize_moysklad_export.py <export.tsv> [--only-rename-map]")
        sys.exit(1)

    src = Path(sys.argv[1])
    only_rename = "--only-rename-map" in sys.argv
    if not src.is_file():
        print(f"Файл не найден: {src}")
        sys.exit(1)

    raw = read_rows(src)
    by_art: dict[str, str] = {}
    for art, name in raw:
        if only_rename and art not in RENAME_MAP:
            continue
        # при дублях артикула оставляем самое длинное наименование
        prev = by_art.get(art)
        if not prev or len(name) > len(prev):
            by_art[art] = name

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["article", "name_long"])
        w.writeheader()
        for art in sorted(by_art.keys()):
            w.writerow({"article": art, "name_long": by_art[art]})

    miss = sorted(set(RENAME_MAP.keys()) - set(by_art.keys())) if only_rename else []
    print(f"Записано: {OUT} ({len(by_art)} артикулов)")
    if miss:
        print(f"MISS для RENAME_MAP ({len(miss)}): {miss[:15]}{'...' if len(miss) > 15 else ''}")
        sys.exit(1)


if __name__ == "__main__":
    main()
