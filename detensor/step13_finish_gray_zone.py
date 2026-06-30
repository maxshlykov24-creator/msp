"""
ШАГ 13 — Закрытие серой зоны (6 товаров вне Сырье/ТИМ).

Действия:
  1. Ocean Pro (US MEDICA) → Категория «Сопутствующие», Линейка «Другое»
  2. Тестовые/дублирующие карточки → archived=true

Запуск:
  python3 step13_finish_gray_zone.py --dry-run
  python3 step13_finish_gray_zone.py
"""

import sys
from ms_client import get_all_products, update_product, make_attr, is_excluded_folder
from config import KATEGORIYA, LINEYKA

DRY_RUN = "--dry-run" in sys.argv

# Явный список — без эвристик, только проверенные 6 позиций.
CATEGORIZE = {
    "Ocean_Pro": {
        "kategoriya": "Сопутствующие",
        "lineyka": "Другое",
    },
}

ARCHIVE_ARTICLES = {
    "stand-simprove-promo",
    "kushetka",
}

ARCHIVE_NAME_MARKERS = [
    "Сенсорный планшет 12.9",
    "HUAWEI Планшет MatePad",
    "Серая подушка на сиденье «Детензор»",
]


def get_category(p) -> str | None:
    for a in p.get("attributes", []):
        if a["name"] == "Категория для аналитики":
            v = a.get("value")
            if isinstance(v, dict):
                return v.get("name")
            return v
    return None


def main():
    print("=" * 70)
    print("ШАГ 13: Закрытие серой зоны")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 70)

    products = get_all_products()
    active = [p for p in products if not p.get("archived") and not is_excluded_folder(p)]
    print(f"Загружено товаров: {len(products)} | в области: {len(active)} активных\n")

    cat_targets = []
    arch_targets = []

    for p in active:
        art = (p.get("article") or "").strip()
        name = p["name"]

        if art in CATEGORIZE and not get_category(p):
            cat_targets.append((p, CATEGORIZE[art]))

        if art in ARCHIVE_ARTICLES:
            arch_targets.append(p)
            continue

        if any(m in name for m in ARCHIVE_NAME_MARKERS):
            arch_targets.append(p)

    print(f"К категоризации: {len(cat_targets)}")
    for p, cfg in cat_targets:
        print(f"  [CAT] art={p.get('article', ''):20} | К={cfg['kategoriya']} | {p['name'][:55]}")

    print(f"\nК архивации: {len(arch_targets)}")
    for p in arch_targets:
        print(f"  [ARC] art={(p.get('article') or '')[:20]:20} | {p['name'][:55]}")

    ok = err = 0

    for p, cfg in cat_targets:
        attrs = [
            make_attr("Категория", {
                "dict_name": "Категория",
                "value_id": KATEGORIYA[cfg["kategoriya"]],
            }),
            make_attr("Линейка", {
                "dict_name": "Линейка",
                "value_id": LINEYKA[cfg["lineyka"]],
            }),
        ]
        label = f"art={p.get('article', ''):20} | {p['name'][:50]}"
        if DRY_RUN:
            print(f"\n  [DRY] CAT {label}")
            ok += 1
            continue
        try:
            update_product(p["id"], {"attributes": attrs})
            print(f"\n  OK    CAT {label}")
            ok += 1
        except Exception as e:
            print(f"\n  ERR   CAT {label} → {e}")
            err += 1

    for p in arch_targets:
        label = f"art={(p.get('article') or '')[:20]:20} | {p['name'][:50]}"
        if DRY_RUN:
            print(f"  [DRY] ARC {label}")
            ok += 1
            continue
        try:
            update_product(p["id"], {"archived": True})
            print(f"  OK    ARC {label}")
            ok += 1
        except Exception as e:
            print(f"  ERR   ARC {label} → {e}")
            err += 1

    print()
    print(f"Итог: OK={ok}, ERR={err}")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
