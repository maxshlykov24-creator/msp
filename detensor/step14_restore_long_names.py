"""
ШАГ 14 — Вернуть длинные названия в name, короткие — в «Название для аналитики».

Группа A (RENAME_MAP): name ← CSV name_long; аналитика ← RENAME_MAP[article].
Группа B (TARGET_ARTICLES без A): name без изменений; аналитика ← name, если пусто.

Запуск:
  python3 step14_restore_long_names.py [--dry-run] [--csv path/to/nomenclature_original.csv]
"""

import csv
import sys
from pathlib import Path

from ms_client import get_all_products, update_product, make_attr, is_excluded_folder
from name_normalize import normalize_dimensions_in_name
from step3_rename import RENAME_MAP
from verify import TARGET_ARTICLES

DRY_RUN = "--dry-run" in sys.argv
DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "nomenclature_original.csv"
FALLBACK_CSV = Path(__file__).resolve().parent / "data" / "nomenclature_original_from_snapshot.csv"


def csv_path() -> Path:
    if "--csv" in sys.argv:
        i = sys.argv.index("--csv")
        return Path(sys.argv[i + 1])
    if DEFAULT_CSV.is_file():
        return DEFAULT_CSV
    if FALLBACK_CSV.is_file():
        return FALLBACK_CSV
    raise SystemExit(
        "Нет nomenclature_original.csv — см. data/README.md и normalize_nomenclature_csv.py"
    )


def load_long_names(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            art = (row.get("article") or "").strip()
            name = (row.get("name_long") or "").strip()
            if art and name:
                out[art] = normalize_dimensions_in_name(name)
    return out


def attr_value(product: dict, attr_name: str):
    for a in product.get("attributes") or []:
        if a.get("name") == attr_name:
            return a.get("value")
    return None


def main() -> None:
    path = csv_path()
    long_by_art = load_long_names(path)

    print("=" * 70)
    print("ШАГ 14: Восстановление длинных name + короткие в аналитику")
    print(f"Режим: {'DRY-RUN' if DRY_RUN else 'БОЕВОЙ'}")
    print(f"CSV: {path} ({len(long_by_art)} строк)")
    print("=" * 70)

    products = get_all_products()
    by_article: dict[str, list] = {}
    for p in products:
        if p.get("archived"):
            continue
        art = (p.get("article") or "").strip()
        if art:
            by_article.setdefault(art, []).append(p)

    miss_a = [a for a in RENAME_MAP if a not in long_by_art]
    if miss_a:
        print(f"MISS в CSV для RENAME_MAP: {len(miss_a)} — {miss_a[:10]}...")
        sys.exit(1)

    stats = {"A_ok": 0, "A_skip": 0, "B_ok": 0, "B_skip": 0, "err": 0, "excluded": 0}

    def apply(p: dict, new_name, analytics: str) -> str:
        """skip | ok | excluded | err"""
        if is_excluded_folder(p):
            stats["excluded"] += 1
            return "excluded"
        payload = {}
        if new_name is not None and p["name"] != new_name:
            payload["name"] = new_name
        cur_an = attr_value(p, "Название для аналитики")
        if analytics and cur_an != analytics:
            payload.setdefault("attributes", []).append(
                make_attr("Название для аналитики", analytics)
            )
        if not payload:
            return "skip"
        art = p.get("article", "")
        label = f"{art:18} name={bool('name' in payload)} anal={analytics[:40]!r}"
        if DRY_RUN:
            print(f"  [DRY] {label}")
            return "ok"
        try:
            update_product(p["id"], payload)
            print(f"  OK    {label}")
            return "ok"
        except Exception as e:
            print(f"  ERR   {label} → {e}")
            stats["err"] += 1
            return "err"

    # Группа A
    for article, short_name in RENAME_MAP.items():
        long_name = long_by_art[article]
        matches = by_article.get(article, [])
        if not matches:
            print(f"  MISS каталог {article}")
            stats["err"] += 1
            continue
        short_norm = normalize_dimensions_in_name(short_name)
        for p in matches:
            r = apply(p, long_name, short_norm)
            if r == "skip":
                stats["A_skip"] += 1
            elif r == "ok":
                stats["A_ok"] += 1

    # Группа B
    group_b = TARGET_ARTICLES - set(RENAME_MAP.keys())
    for article in sorted(group_b):
        for p in by_article.get(article, []):
            cur_an = attr_value(p, "Название для аналитики")
            cur_name = normalize_dimensions_in_name(p["name"])
            r = apply(p, None, cur_name) if not cur_an else "skip"
            if r == "skip":
                stats["B_skip"] += 1
            elif r == "ok":
                stats["B_ok"] += 1

    print()
    print(
        f"Итог: A_ok={stats['A_ok']}, A_skip={stats['A_skip']}, "
        f"B_ok={stats['B_ok']}, B_skip={stats['B_skip']}, "
        f"excluded={stats['excluded']}, ERR={stats['err']}"
    )
    if stats["err"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
