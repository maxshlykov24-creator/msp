"""
ШАГ 11 — Дозаполнение категорий/линеек/жёсткости для оставшихся групп.

Покрывает группы:
  G1  NV-варианты матов Детензор          (5 шт)  → Мат Детензор 18% / Detensor 18% / Жёсткость
  G2  Модули — подспинные части           (10 шт) → Мат Детензор 18% / Detensor 18% / Жёсткость
  G3  Модули — подножные опоры            (4 шт)  → Мат Детензор 18% / Detensor 18% / Жёсткость(где есть)
  G4  Фабричные чехлы на подспинный       (5 шт)  → Аксессуары к мату / Detensor 18%
  G5  Фабричные чехлы на опору для ног    (5 шт)  → Аксессуары к мату / Detensor 18%
  G6  Шейные опоры без артикула FCST      (5 шт)  → Шейная опора / Detensor 18%
  G7  Шейные опоры для ночного сна        (3 шт)  → Шейная опора / Detensor 18%
  G8  Чехлы для матрасов Fibrotop Max     (8 шт)  → Аксессуары к матрасам / Fibrotop Max + Длина
  G9  Чехлы для матрасов Fibrotop Classic (14 шт) → Аксессуары к матрасам / Fibrotop Classic + Длина + Жёсткость
  G10 Чехлы подушки для сидения/спины     (11 шт) → Подушки / Другое
  G11 Чехлы для ночной подушки            (6 шт)  → Подушки / Другое
  G12 Тренажёры Simprove (без артикула)   (5 шт)  → Тренажёры / Другое
  G13 Lumbus аксессуары                   (3 шт)  → Тренажёры / Другое
  G14 O2in / прочие тренажёры             (2 шт)  → Сопутствующие / Другое
  G15 Массажёры / диагностика             (13 шт) → Сопутствующие / Другое
  G18a Внешние/внутренние чехлы к опорам тип 1/2 (4 шт) → Аксессуары к мату / Detensor 18%
  G18b Чехлы для валика Девочка/Мальчик   (2 шт)  → Аксессуары к мату / Detensor 18%

НЕ ТРОГАЕТ:
  G16 — мусор (Apple/Nike/DVD/...) — отдельный скрипт step12_archive_trash.py
  G17 — упаковочное прочее (мешки, коробки, инструкции, стропы)
        — нужно решение владельца

Запуск:
  python3 step11_complete_categories.py --dry-run   # проверить что будет
  python3 step11_complete_categories.py             # боевой запуск
"""

import sys
import re
from ms_client import get_all_products, update_product, make_attr
from config import ZHESTKOST, KATEGORIYA, LINEYKA

DRY_RUN = "--dry-run" in sys.argv


def get_zhestkost_from_name(name: str) -> str | None:
    """Извлечь Ж0/Ж1/Ж2/Ж2+/Ж3 из строки названия.
    Распознаёт: 'Жесткость 0', 'жестк. 0', 'Жесткость 2+', 'жест. 0' и т.п.
    """
    nl = name.lower()
    # 2+ должен искаться ДО 2, иначе "2+" опознается как "2"
    if re.search(r"жест[а-я. ]*2\s*\+|жесткость\s*2\+", nl):
        return "Ж2+"
    if re.search(r"жест[а-я. ]*3", nl):
        return "Ж3"
    if re.search(r"жест[а-я. ]*2", nl):
        return "Ж2"
    if re.search(r"жест[а-я. ]*1", nl):
        return "Ж1"
    if re.search(r"жест[а-я. ]*0", nl):
        return "Ж0"
    return None


def get_length_from_text(text: str) -> int | None:
    """Длина из текста типа 'FIBROTOP MAX 80 * 200' или 'Classic 100 х 200 х 9'."""
    # Ищем число перед '* 200' или 'х 200'
    m = re.search(r"(\d{2,3})\s*[*х×x]\s*200", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def classify(p) -> dict | None:
    """Вернуть классификацию или None если товар нужно оставить."""
    name = p["name"]
    nl = name.lower()
    art = (p.get("article") or "")
    result = {
        "zhestkost": None, "dlina": None,
        "kategoriya": None, "lineyka": None,
        "group": "?",
    }

    # ── G1 NV-варианты ────────────────────────────────────────────────────────
    if ("мат-detensor" in nl or "мат detensor" in nl) and "без валика" in nl:
        result["kategoriya"] = "Мат Детензор 18%"
        result["lineyka"] = "Detensor 18%"
        result["zhestkost"] = get_zhestkost_from_name(name)
        result["dlina"] = 200
        result["group"] = "G1"
        return result

    # ── G2 Подспинные части ───────────────────────────────────────────────────
    if "подспинн" in nl and "фабричн" not in nl and "чехол" not in nl:
        result["kategoriya"] = "Мат Детензор 18%"
        result["lineyka"] = "Detensor 18%"
        result["zhestkost"] = get_zhestkost_from_name(name)
        result["group"] = "G2"
        return result

    # ── G3 Подножные опоры ────────────────────────────────────────────────────
    if ("поднож" in nl or "опора для ног" in nl) and "фабричн" not in nl:
        result["kategoriya"] = "Мат Детензор 18%"
        result["lineyka"] = "Detensor 18%"
        result["zhestkost"] = get_zhestkost_from_name(name)
        result["group"] = "G3"
        return result

    # ── G4 Фабричные чехлы на подспинный ──────────────────────────────────────
    if "фабричн" in nl and "подспинн" in nl:
        result["kategoriya"] = "Аксессуары к мату"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G4"
        return result

    # ── G5 Фабричные чехлы на опору для ног ──────────────────────────────────
    if "фабричн" in nl and ("опору для ног" in nl or "опора для ног" in nl):
        result["kategoriya"] = "Аксессуары к мату"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G5"
        return result

    # ── G7 Шейные опоры для ночного сна (ДО G6, т.к. содержит "опора шейная") ──
    if "опора шейная" in nl and "ночного сна" in nl:
        result["kategoriya"] = "Шейная опора"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G7"
        return result

    # ── G6 Шейные опоры без артикула FCST ────────────────────────────────────
    if "опора шейная" in nl or "фабричный чехол. шейная" in nl:
        result["kategoriya"] = "Шейная опора"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G6"
        return result

    # ── G8 Чехлы для матрасов Fibrotop Max ───────────────────────────────────
    if ("чехол для ночной матрас fibrotop max" in nl or
            "чехол для матрас fibrotop max" in nl):
        result["kategoriya"] = "Аксессуары к матрасам"
        result["lineyka"] = "Fibrotop Max"
        result["dlina"] = get_length_from_text(name)
        result["group"] = "G8"
        return result

    # ── G9 Чехлы для матрасов Fibrotop Classic ───────────────────────────────
    if "чехол для матрас fibrotop classic" in nl:
        result["kategoriya"] = "Аксессуары к матрасам"
        result["lineyka"] = "Fibrotop Classic"
        result["dlina"] = get_length_from_text(name)
        # Жёсткость в названии: "жесткость 1"/"2"/"3" → есть только у 80/90/100
        result["zhestkost"] = get_zhestkost_from_name(name)
        result["group"] = "G9"
        return result

    # ── G10 Чехлы для подушек сидение/спина ──────────────────────────────────
    if "чехол подушка" in nl:
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        result["group"] = "G10"
        return result

    # ── G11 Чехлы для ночной подушки внеш/внутр ──────────────────────────────
    if (("внешний чехол" in nl or "внутренний чехол" in nl)
            and "ночного сна" in nl):
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        result["group"] = "G11"
        return result

    # ── G12 Тренажёры Simprove дубли (без артикула) ──────────────────────────
    if "тренажер сенсомоторный simprove" in nl or "тренажер для тренировки координации" in nl:
        result["kategoriya"] = "Тренажёры"
        result["lineyka"] = "Другое"
        result["group"] = "G12"
        return result

    # ── G13 Lumbus аксессуары ────────────────────────────────────────────────
    if ("lumbus" in nl or "люмбус" in nl) and ("валик" in nl or "чехол" in nl or "коробка" in nl):
        result["kategoriya"] = "Тренажёры"
        result["lineyka"] = "Другое"
        result["group"] = "G13"
        return result

    # ── G14 O2in / дыхательные тренажёры ────────────────────────────────────
    if "o2in" in nl or "дыхательный тренажер" in nl:
        result["kategoriya"] = "Сопутствующие"
        result["lineyka"] = "Другое"
        result["group"] = "G14"
        return result

    # ── G15 Массажёры / диагностика / Сопутствующие ─────────────────────────
    triggers_g15 = [
        "массажер", "массажёр", "массажное кресло",
        "диагностич", "лотос", "омега",
        "lymphanorm", "phlebo", "yamaguchi", "biolift",
        "fudji", "прессотерап", "лимфодренаж",
    ]
    if any(t in nl for t in triggers_g15):
        result["kategoriya"] = "Сопутствующие"
        result["lineyka"] = "Другое"
        result["group"] = "G15"
        return result

    # ── G18a Внешние/внутренние чехлы к опорам тип 1/2 ──────────────────────
    if ("внешний чехол к опоре" in nl or "внутренний чехол к опоре" in nl):
        result["kategoriya"] = "Аксессуары к мату"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G18a"
        return result

    # ── G18b Чехлы для валика Девочка/Мальчик ────────────────────────────────
    if "чехол для валик" in nl:
        result["kategoriya"] = "Аксессуары к мату"
        result["lineyka"] = "Detensor 18%"
        result["group"] = "G18b"
        return result

    return None


def build_payload(cls: dict) -> list:
    """Сформировать список attributes для PUT."""
    attrs = []
    if cls.get("zhestkost"):
        attrs.append(make_attr("Жесткость", {
            "dict_name": "Жесткость",
            "value_id": ZHESTKOST[cls["zhestkost"]],
        }))
    if cls.get("dlina") is not None:
        attrs.append(make_attr("Длина, см", cls["dlina"]))
    if cls.get("kategoriya"):
        attrs.append(make_attr("Категория", {
            "dict_name": "Категория",
            "value_id": KATEGORIYA[cls["kategoriya"]],
        }))
    if cls.get("lineyka"):
        attrs.append(make_attr("Линейка", {
            "dict_name": "Линейка",
            "value_id": LINEYKA[cls["lineyka"]],
        }))
    return attrs


def main():
    print("=" * 70)
    print("ШАГ 11: Дозаполнение категорий для оставшихся групп")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 70)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    active = [p for p in products if not p.get("archived")]

    targets = []
    for p in active:
        # Пропускаем уже категоризированные
        cat_now = None
        for a in p.get("attributes", []):
            if a["name"] == "Категория для аналитики":
                v = a.get("value")
                if isinstance(v, dict):
                    cat_now = v.get("name")
                else:
                    cat_now = v
        if cat_now:
            continue

        cls = classify(p)
        if cls is None:
            continue

        attrs = build_payload(cls)
        if not attrs:
            continue
        targets.append((p, cls, attrs))

    # Сгруппируем для красивого вывода
    by_group = {}
    for p, cls, attrs in targets:
        by_group.setdefault(cls["group"], []).append((p, cls, attrs))

    print(f"\nК обновлению: {len(targets)} товаров в {len(by_group)} группах\n")

    ok = err = 0
    for grp in sorted(by_group.keys()):
        print(f"\n— {grp} ({len(by_group[grp])} шт) —")
        for p, cls, attrs in by_group[grp]:
            zh = cls.get("zhestkost") or "-"
            dl = cls.get("dlina") or "-"
            kt = cls.get("kategoriya") or "-"
            ln = cls.get("lineyka") or "-"
            label = f"Ж={zh:4} Д={str(dl):5} К={kt[:18]:18} Л={ln[:16]:16} | {p['name'][:50]}"
            if DRY_RUN:
                print(f"  [DRY] {label}")
                ok += 1
                continue
            try:
                update_product(p["id"], {"attributes": attrs})
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
