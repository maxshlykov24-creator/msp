"""
Проверка статуса после ручных правок пользователя.
ТОЛЬКО ЧТЕНИЕ — никаких изменений.

Что проверяет:
  1. Что стало с 24 старыми карточками «Устройство медицинское DETENSOR 90/180 х ...»
  2. Текущий статус всех пропущенных групп для нового плана работ
"""

from ms_client import get_all_products


def attr(p, name):
    for a in p.get("attributes", []):
        if a["name"] == name:
            v = a.get("value")
            if isinstance(v, dict):
                return v.get("name", "")
            return v or ""
    return None


def main():
    products = get_all_products()
    active = [p for p in products if not p.get("archived")]
    archived = [p for p in products if p.get("archived")]
    print(f"Всего товаров: {len(products)} | активных: {len(active)} | в архиве: {len(archived)}")
    print()

    # ── 1. Статус 24 старых карточек ───────────────────────────────────────────
    print("=" * 70)
    print("1. Что стало с 24 старыми карточками «Устройство мед. DETENSOR»")
    print("=" * 70)
    old_um = [p for p in products if "Устройство медицинское" in p["name"]]
    active_um = [p for p in old_um if not p.get("archived")]
    archived_um = [p for p in old_um if p.get("archived")]
    print(f"Всего с 'Устройство медицинское': {len(old_um)}")
    print(f"  активных: {len(active_um)}")
    print(f"  в архиве: {len(archived_um)}")
    print()
    print("АКТИВНЫЕ «Устройство медицинское»:")
    for p in active_um:
        cat = attr(p, "Категория для аналитики") or "—"
        print(f"  art='{(p.get('article') or '')[:50]:50}' | К={cat}")
    if archived_um:
        print(f"\nВ АРХИВЕ ({len(archived_um)}):")
        for p in archived_um:
            print(f"  art='{(p.get('article') or '')[:50]}'")
    print()

    # ── 2. Группы товаров для нового плана ─────────────────────────────────────
    print("=" * 70)
    print("2. ТЕКУЩИЕ ГРУППЫ БЕЗ КАТЕГОРИИ (только активные)")
    print("=" * 70)

    groups = {
        "G1 NV-варианты матов Детензор": [],
        "G2 Модули матов (подспинные части)": [],
        "G3 Модули матов (подножные опоры)": [],
        "G4 Фабричные чехлы на подспинный элемент": [],
        "G5 Фабричные чехлы на опору для ног": [],
        "G6 Шейные опоры без артикула FCST": [],
        "G7 Шейные опоры для ночного сна": [],
        "G8 Чехлы для матрасов Fibrotop Max": [],
        "G9 Чехлы для матрасов Fibrotop Classic": [],
        "G10 Чехлы подушки для сидения / для спины": [],
        "G11 Чехлы для ночной подушки (внеш/внутр)": [],
        "G12 Тренажёры Simprove дубли (без артикула)": [],
        "G13 Lumbus аксессуары (Валик/Чехол/Коробка)": [],
        "G14 O2in / прочие тренажёры": [],
        "G15 Массажёры / диагностика / Сопутствующие": [],
        "G16 Тестовые/мусорные товары (Nike, Apple и т.д.)": [],
        "G17 Прочее (инструкции, коробки, мешки)": [],
        "G18 Не классифицировано": [],
    }

    for p in active:
        if attr(p, "Категория для аналитики"):
            continue

        n = p["name"]
        nl = n.lower()
        art = (p.get("article") or "").strip()

        # NV-варианты
        if ("мат-detensor" in nl or "мат detensor" in nl) and "без валика" in nl:
            groups["G1 NV-варианты матов Детензор"].append(p)
        # Модули — подспинные
        elif "подспинн" in nl and ("фабричн" not in nl and "чехол" not in nl):
            groups["G2 Модули матов (подспинные части)"].append(p)
        # Модули — подножные
        elif ("поднож" in nl or "опора для ног" in nl) and "фабричн" not in nl:
            groups["G3 Модули матов (подножные опоры)"].append(p)
        # Фабричные чехлы — подспинный
        elif "фабричн" in nl and "подспинн" in nl:
            groups["G4 Фабричные чехлы на подспинный элемент"].append(p)
        # Фабричные чехлы — опора для ног
        elif "фабричн" in nl and ("опору для ног" in nl or "опора для ног" in nl):
            groups["G5 Фабричные чехлы на опору для ног"].append(p)
        # Шейные опоры
        elif "опора шейная" in nl and "ночного сна" in nl:
            groups["G7 Шейные опоры для ночного сна"].append(p)
        elif "опора шейная" in nl or "фабричный чехол. шейная" in nl:
            groups["G6 Шейные опоры без артикула FCST"].append(p)
        # Чехлы матрасов Max
        elif "чехол для ночной матрас fibrotop max" in nl or "чехол для матрас fibrotop max" in nl:
            groups["G8 Чехлы для матрасов Fibrotop Max"].append(p)
        # Чехлы матрасов Classic
        elif "чехол для матрас fibrotop classic" in nl:
            groups["G9 Чехлы для матрасов Fibrotop Classic"].append(p)
        # Чехлы подушек
        elif "чехол подушка" in nl:
            groups["G10 Чехлы подушки для сидения / для спины"].append(p)
        # Чехлы ночной подушки
        elif ("внешний чехол" in nl or "внутренний чехол" in nl) and "ночного сна" in nl:
            groups["G11 Чехлы для ночной подушки (внеш/внутр)"].append(p)
        # Simprove дубли
        elif "тренажер сенсомоторный simprove" in nl or "тренажер для тренировки координации" in nl:
            groups["G12 Тренажёры Simprove дубли (без артикула)"].append(p)
        # Lumbus аксессуары
        elif "lumbus" in nl or "люмбус" in nl:
            groups["G13 Lumbus аксессуары (Валик/Чехол/Коробка)"].append(p)
        # O2in
        elif "o2in" in nl or "дыхательный тренажер" in nl:
            groups["G14 O2in / прочие тренажёры"].append(p)
        # Массажёры/диагностика
        elif ("массажер" in nl or "массажёр" in nl or "диагностич" in nl or "лотос" in nl
              or "омега" in nl or "lymphanorm" in nl or "phlebo" in nl or "yamaguchi" in nl
              or "biolift" in nl or "массажное кресло" in nl or "fudji" in nl
              or "прессотерап" in nl or "лимфодренаж" in nl):
            groups["G15 Массажёры / диагностика / Сопутствующие"].append(p)
        # Тестовый мусор
        elif (any(brand in n for brand in [
            "Nike", "Apple iPhone", "F.E.A.R.", "Yamaha", "Adidas", "adidas",
            "DVD", "Blu-ray", "(PS3)", "(X360)", "(CD", "Гарри Поттер",
            "Куртка Nike", "Samsung Galaxy", "Toshiba", "Nikon", "TomTom",
            "U218", "U2TC", "Casio", "Wilson", "Minnetonka", "Trefoil",
            "Tenkay", "EVO Design", "AIKO", "MatePad", "PR 100", "Vienna",
            "Сейф для дома", "Хищные пташки", "Зеленый Фонарь", "Lord of the Rings",
            "Теория большого взрыва", "Тайны Смолвиля", "Последний самурай",
            "John Rutter", "Шоссейный  велосипед", "Видеокамера T10", "Scene It",
            "Сверхъестественное", "Я - легенда", "Lumia", "Naushniki", "Koss",
            "Кассовый аппарат", "Сканер HP", "Пункт назначения", "Мячи для гольфа",
            "Brooks", "Феминадзе", "Сенсорный планшет", "HUAWEI", "Кушетка",
            "Стойка напольная", "Тест товар", "Серая подушка на сиденье",
            "Чехол подушка для сидения. Бордовый", "Калькулятор", "Тетрадь",
            "AF-S 14-24", "Новогодний концерт", "Ice Queen", "Папка на кольцах",
            "Шорты", "майка", "Худи", "Хаки", "Джемпер", "Брюки",
        ])):
            groups["G16 Тестовые/мусорные товары (Nike, Apple и т.д.)"].append(p)
        # Прочее (инструкции / коробки / мешки)
        elif ("инструкция" in nl or "сумка детензор" in nl or
              "коробка" in nl or "мешок" in nl or "стропа" in nl or
              "полимер подушка" in nl):
            groups["G17 Прочее (инструкции, коробки, мешки)"].append(p)
        else:
            groups["G18 Не классифицировано"].append(p)

    total_no_cat = 0
    for k, v in groups.items():
        print(f"\n{k} — {len(v)} шт")
        for p in v:
            art = (p.get("article") or "")[:35]
            print(f"  art='{art:35}' | {p['name'][:60]}")
        total_no_cat += len(v)

    print()
    print(f"ИТОГО без категории: {total_no_cat}")


if __name__ == "__main__":
    main()
