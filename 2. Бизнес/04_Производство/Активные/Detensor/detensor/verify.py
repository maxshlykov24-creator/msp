"""
ВЕРИФИКАЦИЯ — проверяет результаты после всех шагов.

Выводит:
  - Сколько целевых товаров имеют правильный основной article (код, не название)
  - Сколько заполнен поле Жёсткость
  - Сколько заполнено Длина, см
  - Сколько заполнена Категория
  - Сколько заполнена Линейка
  - Список проблем (пустые поля у целевых SKU)

Запуск:
  python3 verify.py
"""

import re
from ms_client import get_all_products

TARGET_ARTICLES = {
    # Маты
    "D18H00", "D18H01", "D18H02", "D18H02P", "D18H03",
    # Матрасы Max
    "FBM80","FBM90","FBM100","FBM120","FBM140","FBM160","FBM180","FBM200",
    "FMAX.ORDER",
    # Матрасы Classic (Ж2)
    "FBCL80","FBCL90","FBCL100","FBCL120","FBCL140","FBCL160","FBCL180","FBCL200",
    # Матрасы Classic (Ж1)
    "FBCL80-1","FBCL90-1","FBCL100-1",
    # Матрасы Classic (Ж3)
    "FBCL80-3","FBCL90-3","FBCL100-3","FBCL140-3",
    # Шейные опоры
    "FCST1","FCST2","FCSBG","FCSBB",
    # Подушки для сна
    "NS405010","NS405012","NS405014",
    # Подушки для сидения
    "DSKBL","DSKGR","DSKGO","DSKBR","DSKWH","DSKRE",
    # Подушки для спины
    "DRKBL","DRKGR","DRKGO","DRKBR","DRKWH","DRKRE",
    # Аксессуары к мату
    "HDMALL","HDMFALL","HYGIENE.BACK","HFCST1","HFCST2",
    # Аксессуары к матрасам Classic
    "HFBCL80","HFBCL90","HFBCL100","HFBCL120","HFBCL140","HFBCL160","HFBCL180","HFBCL200",
    # Аксессуары к матрасам Max
    "HFBM80","HFBM90","HFBM100","HFBM120","HFBM140","HFBM160","HFBM180","HFBM200",
    # Тренажёры
    "SIM160-R","SIM160-O","SIM160-B","SIM160-BL","simproveplate","L400PV",
    # Прочие
    "DH.GLASSES","DH.GLASSES-L","BODOFLY","stelki","3DMASK",
    "SILK-G","SILK-R","SILK-P","SILK-B",
    "NIGHT.COVER10","NIGHT.COVER12","NIGHT.COVER14",
    "NIGHT.INSIDE10","NIGHT.INSIDE12",
}

NEEDS_ZHESTKOST = {
    "D18H00","D18H01","D18H02","D18H02P","D18H03",
    "FBM80","FBM90","FBM100","FBM120","FBM140","FBM160","FBM180","FBM200",
    "FBCL80","FBCL90","FBCL100","FBCL120","FBCL140","FBCL160","FBCL180","FBCL200",
    "FBCL80-1","FBCL90-1","FBCL100-1",
    "FBCL80-3","FBCL90-3","FBCL100-3","FBCL140-3",
}

NEEDS_DLINA = {
    "D18H00","D18H01","D18H02","D18H02P","D18H03",
    "FBM80","FBM90","FBM100","FBM120","FBM140","FBM160","FBM180","FBM200",
    "FBCL80","FBCL90","FBCL100","FBCL120","FBCL140","FBCL160","FBCL180","FBCL200",
    "FBCL80-1","FBCL90-1","FBCL100-1",
    "FBCL80-3","FBCL90-3","FBCL100-3","FBCL140-3",
}

def main():
    print("=" * 60)
    print("ВЕРИФИКАЦИЯ результатов")
    print("=" * 60)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    found_in_catalog = set()
    issues = []

    for p in products:
        art = p.get("article", "") or ""
        if art not in TARGET_ARTICLES:
            continue
        found_in_catalog.add(art)

        attr_vals = {}
        old_attr_article = None
        for a in p.get("attributes", []):
            name = a["name"]
            val = a.get("value")
            if name == "Артикул":
                old_attr_article = val
            elif name in ("Жесткость", "Длина, см", "Категория для аналитики", "Линейка"):
                if isinstance(val, dict):
                    val = val.get("name", "")
                attr_vals[name] = val

        row_issues = []
        if old_attr_article:
            row_issues.append(f"доп.Артикул не очищен: {old_attr_article!r}")
        if art in NEEDS_ZHESTKOST and not attr_vals.get("Жесткость"):
            row_issues.append("Жёсткость не заполнена")
        if art in NEEDS_DLINA and not attr_vals.get("Длина, см"):
            row_issues.append("Длина не заполнена")
        if not attr_vals.get("Категория для аналитики"):
            row_issues.append("Категория не заполнена")
        if not attr_vals.get("Линейка"):
            row_issues.append("Линейка не заполнена")

        if row_issues:
            issues.append(f"  {art:20} {p['name'][:35]}")
            for i in row_issues:
                issues.append(f"    → {i}")

    missing = TARGET_ARTICLES - found_in_catalog
    print(f"\nЦелевых SKU найдено в каталоге: {len(found_in_catalog)} из {len(TARGET_ARTICLES)}")
    if missing:
        print(f"Отсутствуют в каталоге ({len(missing)}):")
        for m in sorted(missing):
            print(f"  - {m}")

    print(f"\nПроблем с заполнением: {len(issues)//2}")
    if issues:
        print("\n".join(issues))
    else:
        print("  ✅ Все целевые поля заполнены корректно")

if __name__ == "__main__":
    main()
