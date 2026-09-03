"""
ШАГ 2 — Поменять местами Артикул ↔ доп. поле «Артикул»

Логика:
  - У товаров с заполненным доп. полем «Артикул» (реальный код, напр. D18H00)
    основное поле article содержит длинное название.
  - Нужно: основной article ← код из доп. поля, доп. поле «Артикул» ← очистить.
  - Товары, у которых доп. поле «Артикул» пустое — НЕ трогаем.
  - Если основной article уже равен доп. полю — тоже пропускаем.

Подводные камни:
  1. Артикул в МойСклад НЕ уникален — конфликтов не будет.
  2. Пустая строка '' корректно очищает строковое доп. поле.
  3. Запрос отправляется один раз на товар (article + очистка доп. поля — вместе).
  4. Выводим лог: что было → что стало, с флагом OK/SKIP/ERROR.

Запуск:
  python3 step2_fix_articles.py [--dry-run]
"""

import sys
import json
from ms_client import get_all_products, update_product, make_attr

DRY_RUN = "--dry-run" in sys.argv


def main():
    print("=" * 60)
    print("ШАГ 2: Исправление артикулов")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 60)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    targets = []
    for p in products:
        if p.get("archived"):
            continue
        attr_article = ""
        for a in p.get("attributes", []):
            if a["name"] == "Артикул":
                attr_article = a.get("value", "") or ""
                break
        main_article = p.get("article", "") or ""

        # Нужно действие только если доп. поле заполнено И отличается от основного
        if attr_article and main_article != attr_article:
            targets.append({
                "id": p["id"],
                "name": p["name"],
                "old_main": main_article,
                "new_main": attr_article,
            })

    print(f"Товаров для исправления: {len(targets)}")
    print()

    ok = skip = err = 0
    for t in targets:
        label = f"{t['new_main']:20} | было: {t['old_main'][:40]}"
        if DRY_RUN:
            print(f"  [DRY] {label}")
            ok += 1
            continue
        try:
            payload = {
                "article": t["new_main"],
                "attributes": [make_attr("Артикул", "")],
            }
            result = update_product(t["id"], payload)
            new_article = result.get("article", "")
            if new_article == t["new_main"]:
                print(f"  OK  {label}")
                ok += 1
            else:
                print(f"  WARN article={new_article!r} ≠ {t['new_main']!r}")
                err += 1
        except Exception as e:
            print(f"  ERR {label} → {e}")
            err += 1

    print()
    print(f"Итог: OK={ok}, SKIP={skip}, ERR={err}")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
