"""
ПОЛНЫЙ список товаров без Категории — для детального аудита.
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

    no_cat = []
    for p in active:
        if not attr(p, "Категория для аналитики"):
            no_cat.append(p)

    # Сгруппируем по типу для удобства анализа
    groups = {
        "1. Маты Детензор (модули, без валика)": [],
        "2. Старые карточки «Устройство мед. DETENSOR»": [],
        "3. Подспинные части / опоры для ног / модули": [],
        "4. Фабричные чехлы (аксессуары к мату)": [],
        "5. Шейные опоры (без артикула FCST)": [],
        "6. Тренажёры / Lumbus / Simprove без артикула": [],
        "7. Прочее / сопутствующие": [],
        "8. Не классифицировано (нужно глазами)": [],
    }

    for p in no_cat:
        n = p["name"]
        nl = n.lower()
        art = p.get("article", "") or ""

        if "мат-detensor" in nl or ("мат detensor" in nl and "(без валика" in nl):
            groups["1. Маты Детензор (модули, без валика)"].append(p)
        elif "устройство медицинское detensor" in nl and ("180 х" in nl or "90 х" in nl or "мат" in art.lower()):
            groups["2. Старые карточки «Устройство мед. DETENSOR»"].append(p)
        elif "подспинн" in nl or "поднож" in nl or "опора для ног" in nl or "подножная" in nl:
            if "фабричн" in nl or "чехол" in nl:
                groups["4. Фабричные чехлы (аксессуары к мату)"].append(p)
            else:
                groups["3. Подспинные части / опоры для ног / модули"].append(p)
        elif "опора шейная" in nl or "шейная опора" in nl:
            groups["5. Шейные опоры (без артикула FCST)"].append(p)
        elif "lumbus" in nl or "люмбус" in nl or "simprove" in nl or "тренажер" in nl:
            groups["6. Тренажёры / Lumbus / Simprove без артикула"].append(p)
        elif ("инструкция" in nl or "сумка" in nl or "коробка" in nl or
              "стропа" in nl or "массажер" in nl or "диагностич" in nl or
              "fudji" in nl):
            groups["7. Прочее / сопутствующие"].append(p)
        else:
            groups["8. Не классифицировано (нужно глазами)"].append(p)

    for label, items in groups.items():
        print(f"\n{'=' * 70}")
        print(f"{label} — {len(items)} шт")
        print('=' * 70)
        for p in items:
            art = p.get("article", "") or ""
            attr_art = ""
            for a in p.get("attributes", []):
                if a["name"] == "Артикул":
                    attr_art = a.get("value", "") or ""
            print(f"  id={p['id'][:8]} | art='{art[:40]}' | attr_art='{attr_art}' | {p['name'][:55]}")


if __name__ == "__main__":
    main()
