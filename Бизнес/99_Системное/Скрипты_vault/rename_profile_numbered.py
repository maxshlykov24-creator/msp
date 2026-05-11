#!/usr/bin/env python3
# Одноразово: нумерация каталогов под Личное/1. Профиль и корень Личное.
from __future__ import annotations

import os
from pathlib import Path

BASE_PROFILE = Path("/Users/max/Desktop/CURSOR/Личное/1. Профиль")
BASE_LICHNOE = Path("/Users/max/Desktop/CURSOR/Личное")


def norm(s: str) -> str:
    return s.replace("_", " ")


def child_dirs(p: Path) -> list[Path]:
    if not p.is_dir():
        return []
    return sorted([x for x in p.iterdir() if x.is_dir()], key=lambda x: x.name)


def rel_map_profile() -> dict[str, str]:
    m: dict[str, str] = {}

    def a(old: str, new: str):
        m[old] = new

    # Верх профиля
    a("Отчёты", "1. Отчёты")
    a("Дух", "2. Дух")
    a("Разум", "3. Разум")
    a("Тело", "4. Тело")
    a("Документы", "5. Документы")
    a("Прочее", "6. Прочее")

    RO, RN = "Отчёты", "1. Отчёты"
    a(f"{RO}/Открывашка", f"{RN}/1.1. Открывашка")
    a(f"{RO}/Закрывашка", f"{RN}/1.2. Закрывашка")
    a(f"{RO}/Планирование", f"{RN}/1.3. Планирование")
    PO, PN = f"{RO}/Планирование", f"{RN}/1.3. Планирование"
    plan = ["Неделя", "Месяц", "03_месяца", "Год", "05_лет", "30_лет", "Прочее"]
    for j, s in enumerate(plan, 1):
        a(f"{PO}/{s}", f"{PN}/1.3.{j}. {norm(s)}")

    DO, DN = "Дух", "2. Дух"
    a(f"{DO}/Изучение", f"{DN}/2.1. Изучение")
    a(f"{DO}/Проповеди", f"{DN}/2.2. Проповеди")
    a(f"{DO}/Служение", f"{DN}/2.3. Служение")
    DIO, DIN = f"{DO}/Изучение", f"{DN}/2.1. Изучение"
    a(f"{DIO}/Библия", f"{DIN}/2.1.1. Библия")
    a(f"{DIO}/Вопросы", f"{DIN}/2.1.2. Вопросы")
    a(f"{DIO}/Спасение", f"{DIN}/2.1.3. Спасение")
    a(f"{DIO}/Библия/С_комментарием", f"{DIN}/2.1.1. Библия/1. С комментарием")
    SO, SN = f"{DO}/Служение", f"{DN}/2.3. Служение"
    a(f"{SO}/Мое_слово", f"{SN}/2.3.1. Мое слово")
    a(f"{SO}/Церковь", f"{SN}/2.3.2. Церковь")
    CO, CN = f"{SO}/Церковь", f"{SN}/2.3.2. Церковь"
    a(f"{CO}/Мои_служения", f"{CN}/1. Мои служения")
    a(f"{CO}/Порядок", f"{CN}/2. Порядок")
    MO, MN = f"{CO}/Мои_служения", f"{CN}/1. Мои служения"
    for j, s in enumerate(["Бизнес", "Семьи", "Футбол"], 1):
        a(f"{MO}/{s}", f"{MN}/{j}. {norm(s)}")
    PrO, PrN = f"{CO}/Порядок", f"{CN}/2. Порядок"
    for j, s in enumerate(sorted(["Зона_встречи", "Зона_контроля", "Зона_расстановки", "Зона_склада"]), 1):
        a(f"{PrO}/{s}", f"{PrN}/{j}. {norm(s)}")

    RzO, RzN = "Разум", "3. Разум"
    rz_children = [
        "Изучение",
        "Инсайты",
        "Кино",
        "Люди",
        "Музыка",
        "Путешествия",
        "Рестораны",
        "Семья",
        "Финансы",
        "Хотелки",
    ]
    for i, s in enumerate(rz_children, 1):
        if i == 1:
            nn = f"3.1. {norm(s)}"
        elif i == 10:
            nn = f"3.10. {norm(s)}"
        else:
            nn = f"3.{i}. {norm(s)}"
        a(f"{RzO}/{s}", f"{RzN}/{nn}")

    # 3.1. Изучение -> 3.1.k. (внутри Нейросети глубже не кодируем)
    IzO = f"{RzO}/Изучение"
    IzN = f"{RzN}/3.1. Изучение"
    iz_subs = ["Бизнес", "Заметки", "Книги", "Курсы", "Нейросети", "Развитие"]
    for j, s in enumerate(iz_subs, 1):
        a(f"{IzO}/{s}", f"{IzN}/3.1.{j}. {norm(s)}")
    NeO, NeN = f"{IzO}/Нейросети", f"{IzN}/3.1.5. Нейросети"
    a(f"{NeO}/00_Учебные_материалы", f"{NeN}/1. Учебные материалы")
    UO, UN = f"{NeO}/00_Учебные_материалы", f"{NeN}/1. Учебные материалы"
    a(f"{UO}/Материалы_для_ученика", f"{UN}/1. Материалы для ученика")
    a(f"{UO}/Обучение Cursor", f"{UN}/2. Обучение Cursor")

    LuO, LuN = f"{RzO}/Люди", f"{RzN}/3.4. Люди"
    for j, s in enumerate(["Друзья", "Подарки", "Родственники"], 1):
        a(f"{LuO}/{s}", f"{LuN}/3.4.{j}. {norm(s)}")

    PtO, PtN = f"{RzO}/Путешествия", f"{RzN}/3.6. Путешествия"
    for j, s in enumerate(["Билеты", "Визы", "Впечатления", "Идеи_поездок"], 1):
        a(f"{PtO}/{s}", f"{PtN}/3.6.{j}. {norm(s)}")

    SmO, SmN = f"{RzO}/Семья", f"{RzN}/3.8. Семья"
    a(f"{SmO}/Воспитание_детей", f"{SmN}/3.8.1. Воспитание детей")

    TO, TN = "Тело", "4. Тело"
    a(f"{TO}/Здоровье", f"{TN}/4.1. Здоровье")
    a(f"{TO}/Спорт", f"{TN}/4.2. Спорт")
    Zo, Zn = f"{TO}/Здоровье", f"{TN}/4.1. Здоровье"
    for j, s in enumerate(
        sorted(["Анализы", "Добавки_и_витамины", "Ледяные_ванны", "План_питания", "Сон"]),
        1,
    ):
        a(f"{Zo}/{s}", f"{Zn}/4.1.{j}. {norm(s)}")
    a(f"{TO}/Спорт/Тренировки", f"{TN}/4.2. Спорт/4.2.1. Тренировки")

    DOc, DNc = "Документы", "5. Документы"
    for j, s in enumerate(["Договоры", "Медицина", "Налоги", "Паспорта"], 1):
        a(f"{DOc}/{s}", f"{DNc}/5.{j}. {norm(s)}")

    return m


def pairs_sorted() -> list[tuple[Path, Path]]:
    m = rel_map_profile()
    out = [(BASE_PROFILE / k.replace("/", os.sep), BASE_PROFILE / v.replace("/", os.sep)) for k, v in m.items()]
    out.sort(key=lambda t: -str(t[0]).count(os.sep))
    return out


def main():
    for old_p, new_p in pairs_sorted():
        if not old_p.is_dir():
            print("SKIP (no dir)", old_p)
            continue
        if new_p.exists():
            print("SKIP (exists)", new_p)
            continue
        new_p.parent.mkdir(parents=True, exist_ok=True)
        print("MV", old_p)
        old_p.rename(new_p)

    mapping = {"00_Входящее": "0. Входящее", "01_Профиль": "1. Профиль", "02_Тесты": "2. Тесты"}
    for o, n in mapping.items():
        op, np = BASE_LICHNOE / o, BASE_LICHNOE / n
        if op.exists() and not np.exists():
            print("MV", op, "->", np)
            op.rename(np)


if __name__ == "__main__":
    main()
