"""
ШАГ 12 — Архивация тестового/мусорного контента в каталоге.

Архивирует товары, заведомо не относящиеся к продукции DETENSOR
(остались от тестовой загрузки): Apple iPhone, Nike, DVD/Blu-ray,
Adidas, Yamaha колонки, кассовые аппараты, ноутбуки и т.п.

В работе использует флаг archived=true (НЕ удаляет физически).
Архивацию можно отменить вручную через UI МойСклад.

ВАЖНО: список товаров определяется по списку маркеров в названии.
Любое сомнительное сначала смотрим глазами через --dry-run.

Запуск:
  python3 step12_archive_trash.py --dry-run   # просмотр
  python3 step12_archive_trash.py             # боевой
"""

import sys
from ms_client import get_all_products, update_product, is_excluded_folder

DRY_RUN = "--dry-run" in sys.argv

# Точные подстроки названий, которые гарантированно являются мусором
TRASH_MARKERS = [
    "Гарри Поттер",
    "Калькулятор Casio",
    "Тетрадь на кольцах",
    "AF-S 14-24mm",
    "Новогодний концерт",
    "Ice Queen",
    "Папка на кольцах",
    "F.E.A.R. 3",
    "Minnetonka",
    "женские шорты из джерси Nike",
    "майка Nike",
    "Хищные пташки",
    "Trefoil Hoodie",
    "Apple iPhone 4S",
    "Теория большого взрыва",
    "Шоссейный  велосипед",
    "Black and White America",
    "Сейф для дома AIKO",
    "Nikon D3100",
    "Batman: Аркхем",
    "Nike Tenkay",
    "Yamaha PDX",
    "Видеокамера T10",
    "Кассовый аппарат Casio",
    "Сканер HP Scanjet",
    "Пункт назначения",
    "Мячи для гольфа",
    "Женская футболка Nike",
    "Футболка Adidas",
    "Брюки для разогрева Adidas",
    "Тайны Смолвиля",
    "LED HD телевизор Toshiba",
    "домашний кинотеатр HT-D6500W",
    "Lord of the Rings",
    "Scene It",
    "U218 Singles",
    "Trefoil Logo",
    "Зеленый Фонарь",
    "Женские кварцевые часы PR",
    "Последний самурай",
    "John Rutter",
    "Куртка Nike Chambray",
    "GPS-навигатор TomTom",
    "Samsung Galaxy Player",
    "Сверхъестественное:",
    "Я - легенда (DVD)",
    "Nokia Lumia",
    "Наушники Koss",
    "EVO Design 4G",
    "Crossroads Guitar",
    "Tенкай Slip",
    "Шорты Trefoil",
    "Худи Trefoil",
    "Тест товар",
    # 17 шт «не классифицировано», тоже мусор:
    "Портретный объектив",
    "12 шагов к построению бизнеса",
    "LED телевизор 8000 серии",
    "Шестиместная палатка",
    "Evolve SST",
    "Ноутбук Samsung серии 9",
    "adizero Rush",
    "Легенды ночных стражей",
    "Feeding the Monkies",
    "Montblanc Contemporary",
]


def is_trash(name: str) -> bool:
    return any(m in name for m in TRASH_MARKERS)


def main():
    print("=" * 70)
    print("ШАГ 12: Архивация тестовых/мусорных товаров")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 70)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    # Товары из Сырье/ТИМ не трогаем по решению владельца — даже если они мусор.
    targets = [
        p for p in products
        if not p.get("archived")
        and is_trash(p["name"])
        and not is_excluded_folder(p)
    ]
    excluded_trash = sum(
        1 for p in products
        if not p.get("archived") and is_trash(p["name"]) and is_excluded_folder(p)
    )
    print(f"К архивации: {len(targets)}")
    if excluded_trash:
        print(
            f"Пропущено (Сырье/ТИМ): {excluded_trash} — мусор в исключённых папках, "
            "не трогаем по решению владельца"
        )
    if len(targets) == 0:
        print("Скрипт no-op: все мусорные товары находятся в исключённых папках "
              "(Сырье / Товары интернет-магазинов) и не трогаются.")
    print()

    ok = err = 0
    for p in targets:
        label = f"art={(p.get('article') or '')[:20]:20} | {p['name'][:60]}"
        if DRY_RUN:
            print(f"  [DRY] {label}")
            ok += 1
            continue
        try:
            update_product(p["id"], {"archived": True})
            print(f"  OK    {label}")
            ok += 1
        except Exception as e:
            print(f"  ERR   {label} → {e}")
            err += 1

    print()
    print(f"Итог: OK={ok}, ERR={err}")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
