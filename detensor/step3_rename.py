"""
ШАГ 3 — Переименовать товары с длинными/техническими названиями

Работает на основании маппинга article → новое название.
Запускать ПОСЛЕ Шага 2 (чтобы основной article уже был кодом).

Подводные камни:
  1. Несколько товаров могут иметь одинаковый article (напр. NIGHT.INSIDE10 — 2 шт).
     Для таких применяем переименование ко всем совпадениям, пишем предупреждение.
  2. Если товар уже имеет правильное название — пропускаем (idempotent).
  3. Матрасы Classic не имеют базового варианта Ж1/Ж3 для всех размеров — только
     80, 90, 100 имеют все 3 жёсткости. 120-200 имеют только Ж2.

Запуск:
  python3 step3_rename.py [--dry-run]
"""

import sys
from ms_client import get_all_products, update_product

DRY_RUN = "--dry-run" in sys.argv

# ── Маппинг article → новое название ──────────────────────────────────────────
RENAME_MAP = {
    # Маты Детензор 18%
    "D18H00":  "Мат Детензор 18% Жёсткость 0 (полный комплект)",
    "D18H01":  "Мат Детензор 18% Жёсткость 1 (полный комплект)",
    "D18H02":  "Мат Детензор 18% Жёсткость 2 (полный комплект)",
    "D18H02P": "Мат Детензор 18% Жёсткость 2+ (полный комплект)",
    "D18H03":  "Мат Детензор 18% Жёсткость 3 (полный комплект)",

    # Матрасы Fibrotop MAX
    "FBM80":  "Матрас Fibrotop Max 80×200×18",
    "FBM90":  "Матрас Fibrotop Max 90×200×18",
    "FBM100": "Матрас Fibrotop Max 100×200×18",
    "FBM120": "Матрас Fibrotop Max 120×200×18",
    "FBM140": "Матрас Fibrotop Max 140×200×18",
    "FBM160": "Матрас Fibrotop Max 160×200×18",
    "FBM180": "Матрас Fibrotop Max 180×200×18",
    "FBM200": "Матрас Fibrotop Max 200×200×18",
    "FMAX.ORDER": "Матрас Fibrotop Max (под заказ)",

    # Матрасы Fibrotop Classic — жёсткость 1
    "FBCL80-1":  "Матрас Fibrotop Classic 80×200×9 Жёсткость 1",
    "FBCL90-1":  "Матрас Fibrotop Classic 90×200×9 Жёсткость 1",
    "FBCL100-1": "Матрас Fibrotop Classic 100×200×9 Жёсткость 1",

    # Матрасы Fibrotop Classic — жёсткость 2 (базовые)
    "FBCL80":  "Матрас Fibrotop Classic 80×200×9 Жёсткость 2",
    "FBCL90":  "Матрас Fibrotop Classic 90×200×9 Жёсткость 2",
    "FBCL100": "Матрас Fibrotop Classic 100×200×9 Жёсткость 2",
    "FBCL120": "Матрас Fibrotop Classic 120×200×9 Жёсткость 2",
    "FBCL140": "Матрас Fibrotop Classic 140×200×9 Жёсткость 2",
    "FBCL160": "Матрас Fibrotop Classic 160×200×9 Жёсткость 2",
    "FBCL180": "Матрас Fibrotop Classic 180×200×9 Жёсткость 2",
    "FBCL200": "Матрас Fibrotop Classic 200×200×9 Жёсткость 2",

    # Матрасы Fibrotop Classic — жёсткость 3
    "FBCL80-3":  "Матрас Fibrotop Classic 80×200×9 Жёсткость 3",
    "FBCL90-3":  "Матрас Fibrotop Classic 90×200×9 Жёсткость 3",
    "FBCL100-3": "Матрас Fibrotop Classic 100×200×9 Жёсткость 3",
    "FBCL140-3": "Матрас Fibrotop Classic 140×200×9 Жёсткость 3",

    # Шейные опоры
    "FCST1": "Шейная опора Детензор Тип 1 (18×7×40)",
    "FCST2": "Шейная опора Детензор Тип 2 (14×6×35)",
    "FCSBG": "Шейная опора Детензор BABY-GIRL",
    "FCSBB": "Шейная опора Детензор BABY-BOY",

    # Подушки для сна
    "NS405010": "Подушка для ночного сна Детензор 40×50×10 см",
    "NS405012": "Подушка для ночного сна Детензор 40×50×12 см",
    "NS405014": "Подушка для ночного сна Детензор 40×50×14 см",

    # Подушки для сидения DSK
    "DSKBL": "Подушка для сидения Детензор Чёрная",
    "DSKGR": "Подушка для сидения Детензор Серая",
    "DSKGO": "Подушка для сидения Детензор Золотая",
    "DSKBR": "Подушка для сидения Детензор Коричневая",
    "DSKWH": "Подушка для сидения Детензор Ванильная",
    "DSKRE": "Подушка для сидения Детензор Бордовая",

    # Подушки для спины DRK
    "DRKBL": "Подушка для спины Детензор Чёрная",
    "DRKGR": "Подушка для спины Детензор Серая",
    "DRKGO": "Подушка для спины Детензор Золотая",
    "DRKBR": "Подушка для спины Детензор Коричневая",
    "DRKWH": "Подушка для спины Детензор Ванильная",
    "DRKRE": "Подушка для спины Детензор Бордовая",

    # Аксессуары к мату
    "HDMALL":      "Гигиенические чехлы (комплект к мату Детензор)",
    "HDMFALL":     "Фабричные чехлы (комплект к мату Детензор)",
    "HYGIENE.BACK": "Гигиенический чехол Детензор-HYGIENE для подспинной части",
    "HFCST1":      "Гигиенический чехол. Шейная опора Тип 1",
    "HFCST2":      "Гигиенический чехол. Шейная опора Тип 2",

    # Аксессуары к матрасам Fibrotop Classic
    "HFBCL80":  "Простынь на матрас Fibrotop Classic 80×200×9",
    "HFBCL90":  "Простынь на матрас Fibrotop Classic 90×200×9",
    "HFBCL100": "Простынь на матрас Fibrotop Classic 100×200×9",
    "HFBCL120": "Простынь на матрас Fibrotop Classic 120×200×9",
    "HFBCL140": "Простынь на матрас Fibrotop Classic 140×200×9",
    "HFBCL160": "Простынь на матрас Fibrotop Classic 160×200×9",
    "HFBCL180": "Простынь на матрас Fibrotop Classic 180×200×9",
    "HFBCL200": "Простынь на матрас Fibrotop Classic 200×200×9",

    # Аксессуары к матрасам Fibrotop Max
    "HFBM80":  "Простынь на матрас Fibrotop Max 80×200×18",
    "HFBM90":  "Простынь на матрас Fibrotop Max 90×200×18",
    "HFBM100": "Простынь на матрас Fibrotop Max 100×200×18",
    "HFBM120": "Простынь на матрас Fibrotop Max 120×200×18",
    "HFBM140": "Простынь на матрас Fibrotop Max 140×200×18",
    "HFBM160": "Простынь на матрас Fibrotop Max 160×200×18",
    "HFBM180": "Простынь на матрас Fibrotop Max 180×200×18",
    "HFBM200": "Простынь на матрас Fibrotop Max 200×200×18",

    # Тренажёры
    "SIM160-R":    "Тренажёр сенсомоторный Simprove 160 (красный)",
    "SIM160-O":    "Тренажёр сенсомоторный Simprove 160 (оранжевый)",
    "SIM160-B":    "Тренажёр сенсомоторный Simprove 160 (чёрный)",
    "SIM160-BL":   "Тренажёр сенсомоторный Simprove 160 (синий)",
    "simproveplate": "Тренажёр сенсомоторный Simprove Plate",
    "L400PV":      "Корректор поясничного отдела Lumbus L400PV",

    # Прочие аксессуары
    "DH.GLASSES":   "Очки-перископы для чтения лёжа Детензор-EYE",
    "DH.GLASSES-L": "Очки-перископы для чтения лёжа Детензор-EYE Light",
    "BODOFLY":      "Массажёр для ног Bodo Fly",
    "stelki":       "Индивидуальные стельки",
    "3DMASK":       "3D маска для ночного сна",
    "SILK-G":       "Шёлковая маска для сна (серая)",
    "SILK-R":       "Шёлковая маска для сна (красная)",
    "SILK-P":       "Шёлковая маска для сна (розовая)",
    "SILK-B":       "Шёлковая маска для сна (чёрная)",
}


def main():
    print("=" * 60)
    print("ШАГ 3: Переименование товаров")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 60)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    # Построим индекс article → [продукт, ...]
    by_article: dict[str, list] = {}
    for p in products:
        if p.get("archived"):
            continue
        art = p.get("article", "") or ""
        if art:
            by_article.setdefault(art, []).append(p)

    ok = skip = warn = err = 0
    for article, new_name in RENAME_MAP.items():
        matches = by_article.get(article, [])
        if not matches:
            print(f"  MISS  {article} — не найден в каталоге")
            continue
        if len(matches) > 1:
            print(f"  WARN  {article} — найдено {len(matches)} товаров с таким артикулом!")
            warn += 1
        for p in matches:
            current_name = p["name"]
            if current_name == new_name:
                print(f"  SKIP  {article} — название уже верное")
                skip += 1
                continue
            label = f"{article:20} | {current_name[:35]} → {new_name[:35]}"
            if DRY_RUN:
                print(f"  [DRY] {label}")
                ok += 1
                continue
            try:
                result = update_product(p["id"], {"name": new_name})
                if result.get("name") == new_name:
                    print(f"  OK    {label}")
                    ok += 1
                else:
                    print(f"  WARN  name={result.get('name')!r}")
                    err += 1
            except Exception as e:
                print(f"  ERR   {label} → {e}")
                err += 1

    print()
    print(f"Итог: OK={ok}, SKIP={skip}, WARN={warn}, ERR={err}")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
