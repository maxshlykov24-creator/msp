"""Импорт номенклатуры из CSV в БД + выгрузка на утверждение.

Использование:
    python -m scripts.build_catalog "/path/Товары.csv" [--db data/kombikorm.db] [--replace] [--all]

--all      — импортировать не только активную розницу (Статус=да), а всё.
--replace  — очистить products/aliases перед импортом.

После импорта пишет файл catalog_review.md рядом с проектом — для проверки
пользователем (короткие имена + цены + размеры + алиасы).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.catalog import parse_csv  # noqa: E402
from app.config import load_config  # noqa: E402


def write_review(products: list[dict], out_path: Path) -> None:
    lines = [
        "# Каталог на утверждение (активная розница)",
        "",
        f"Всего позиций: {len(products)}",
        "",
        "| # | Наименование | Мешок, кг | Цена, ₽ | Алиасы (для голоса) |",
        "|---|--------------|-----------|---------|---------------------|",
    ]
    for i, p in enumerate(products, 1):
        size = f"{p['bag_size_kg']:g}" if p["bag_size_kg"] else "—"
        price = f"{p['price_bag']:g}" if p["price_bag"] else "—"
        aliases = ", ".join(p["aliases"][:6])
        lines.append(f"| {i} | {p['canonical_name']} | {size} | {price} | {aliases} |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="путь к CSV номенклатуры")
    ap.add_argument("--db", default=None, help="путь к БД (по умолчанию из конфига)")
    ap.add_argument("--replace", action="store_true", help="очистить каталог перед импортом")
    ap.add_argument("--all", action="store_true", help="импортировать всё, не только активную розницу")
    args = ap.parse_args()

    config = load_config()
    db_path = Path(args.db) if args.db else config.db_path
    conn = db.connect(db_path)
    db.init_db(conn)

    products = parse_csv(args.csv, only_active=not args.all)
    print(f"Разобрано позиций: {len(products)}")

    existing = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if existing and not args.replace:
        print(f"В БД уже {existing} позиций. Запустите с --replace, чтобы перезаписать.")
    else:
        if args.replace:
            with db.transaction(conn):
                conn.execute("DELETE FROM aliases")
                conn.execute("DELETE FROM products")
        db.bulk_insert_products(conn, products)
        print(f"Импортировано в БД: {db_path}")

    review = Path(__file__).resolve().parent.parent / "catalog_review.md"
    write_review(products, review)
    print(f"Файл на утверждение: {review}")


if __name__ == "__main__":
    main()
