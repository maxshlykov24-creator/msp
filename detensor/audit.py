"""
АУДИТ — глубокая сверка с ТЗ.
Проверяет:
  1. Покрытие шага 6 (Категория — все ~128 SKU)
  2. Наличие модулей матов (подспинные/подножные/валики)
  3. -NV варианты (мат Детензор без валика)
  4. Аренда (rent28_*)
  5. Шаг 8 — архивация 7 указанных SKU
  6. Шаг 9 — дубли наволочек
  7. Корректность Жёсткости у Fibrotop Classic (по суффиксу)
"""

import json
from ms_client import get_all_products, is_excluded_folder


def main():
    products = get_all_products()
    print(f"Всего товаров: {len(products)}")
    active = [p for p in products if not p.get("archived")]
    archived = [p for p in products if p.get("archived")]
    excluded = [p for p in active if is_excluded_folder(p)]
    in_scope = [p for p in active if not is_excluded_folder(p)]
    print(f"Активных: {len(active)}, в архиве: {len(archived)}")
    print(f"Исключено (Сырье/ТИМ): {len(excluded)} — не проверяем и не трогаем")
    print(f"В области проверки: {len(in_scope)}")
    print()

    # ── Соберём индекс ─────────────────────────────────────────────────────────
    by_id = {p["id"]: p for p in products}

    def attr(p, name):
        for a in p.get("attributes", []):
            if a["name"] == name:
                v = a.get("value")
                if isinstance(v, dict):
                    return v.get("name", "")
                return v or ""
        return None

    # ─── 1. Покрытие Категории (только товары вне Сырье/ТИМ) ─────────────────
    print("=" * 70)
    print("1. ПОКРЫТИЕ ШАГА 6: Категория для аналитики (вне Сырье/ТИМ)")
    print("=" * 70)
    no_cat = []
    has_cat = []
    for p in in_scope:
        cat = attr(p, "Категория для аналитики")
        if cat:
            has_cat.append((p["name"], p.get("article", ""), cat))
        else:
            no_cat.append(p)

    print(f"В области проверки с Категорией: {len(has_cat)}")
    print(f"В области проверки без Категории: {len(no_cat)}")
    print()
    if no_cat:
        print("Товары БЕЗ категории (первые 40):")
        for p in no_cat[:40]:
            print(f"  art={p.get('article','')[:25]:25} name={p['name'][:60]}")
        if len(no_cat) > 40:
            print(f"  ... ещё {len(no_cat)-40}")
    print()

    # ─── 2. Модули матов ──────────────────────────────────────────────────────
    print("=" * 70)
    print("2. МОДУЛИ МАТОВ (подспинные / подножные)")
    print("=" * 70)
    modules = []
    for p in active:
        n = p["name"].lower()
        if "подспинн" in n or "поднож" in n or "валик" in n.replace("валик lumbus", "").replace("валик под поясницу люмбус", ""):
            modules.append(p)
    for p in modules:
        cat = attr(p, "Категория для аналитики") or "—"
        lin = attr(p, "Линейка") or "—"
        zh = attr(p, "Жесткость") or "—"
        print(f"  art={p.get('article','')[:25]:25} name={p['name'][:45]:45} | К={cat:20} Л={lin:15} Ж={zh}")
    print(f"\nВсего модулей: {len(modules)}")
    print()

    # ─── 3. -NV варианты ──────────────────────────────────────────────────────
    print("=" * 70)
    print("3. -NV ВАРИАНТЫ матов Детензор (без валика)")
    print("=" * 70)
    nv = []
    for p in active:
        art = p.get("article", "") or ""
        for a in p.get("attributes", []):
            if a["name"] == "Артикул":
                old = a.get("value", "") or ""
                if "NV" in old:
                    nv.append((p, "доп.Артикул"))
        if art and ("-NV" in art or "NV" in art.upper()):
            nv.append((p, "main.article"))
    if not nv:
        print("  -NV вариантов НЕ найдено в каталоге")
    for p, src in nv:
        print(f"  [{src}] art={p.get('article','')} name={p['name'][:60]}")
    print()

    # ─── 4. Аренда ────────────────────────────────────────────────────────────
    print("=" * 70)
    print("4. АРЕНДА (rent28_* / m1/m2/m2+/m3)")
    print("=" * 70)
    rent = []
    for p in active:
        art = (p.get("article") or "").lower()
        n = p["name"].lower()
        if "rent" in art or "rent" in n or "аренд" in n:
            rent.append(p)
    if not rent:
        print("  АРЕНДЫ в каталоге НЕТ. По ТЗ должно быть 12 SKU.")
    for p in rent:
        cat = attr(p, "Категория для аналитики") or "—"
        lin = attr(p, "Линейка") or "—"
        print(f"  art={p.get('article','')[:25]:25} name={p['name'][:50]} | К={cat} Л={lin}")
    print()

    # ─── 5. Шаг 8 — Архивация ─────────────────────────────────────────────────
    print("=" * 70)
    print("5. ШАГ 8 — Архивация 7 SKU (по ТЗ)")
    print("=" * 70)
    targets_step8 = [
        "Тренажер Propriomed 1",
        "Тренажер Propriomed 2",
        "Тренажер Propriomed 100",
        "Настенный держатель для тренажеров Propriomed",
        "Пакет «Профессиональный» для Партнёров",
        "Пакет «Уверенный старт» для Партнёров",
        "Сиденье автомобильное на тумбе",
    ]
    for target in targets_step8:
        found = []
        for p in products:
            if target.lower() in p["name"].lower():
                found.append(p)
        if not found:
            print(f"  MISS  '{target}' — не найден в каталоге")
        for p in found:
            status = "архив" if p.get("archived") else "АКТИВЕН"
            print(f"  {status:8} {p['name'][:60]}")
    print()

    # ─── 6. Шаг 9 — Дубли наволочек ───────────────────────────────────────────
    print("=" * 70)
    print("6. ШАГ 9 — Дубли наволочек для ночной подушки (с ценой 0)")
    print("=" * 70)
    napkins = []
    for p in products:
        if "наволоч" in p["name"].lower() and "ночн" in p["name"].lower():
            napkins.append(p)
    for p in napkins:
        sale_prices = p.get("salePrices", [])
        price = sale_prices[0].get("value", 0) / 100 if sale_prices else 0
        status = "архив" if p.get("archived") else "АКТИВЕН"
        cat = attr(p, "Категория для аналитики") or "—"
        print(f"  {status:8} цена={price:>8.0f} | art={p.get('article','')[:25]:25} | {p['name'][:50]} | К={cat}")
    print(f"\nВсего наволочек: {len(napkins)}")
    print()

    # ─── 7. Fibrotop Classic — проверка Жёсткости ────────────────────────────
    print("=" * 70)
    print("7. FIBROTOP CLASSIC — проверка Жёсткости по суффиксу")
    print("=" * 70)
    print("ТЗ: '-1' → Ж1, без суффикса → Ж2, '-3' → Ж3")
    for p in active:
        art = p.get("article", "") or ""
        if art.startswith("FBCL"):
            zh = attr(p, "Жесткость") or "—"
            dl = attr(p, "Длина, см") or "—"
            kat = attr(p, "Категория для аналитики") or "—"
            lin = attr(p, "Линейка") or "—"
            expected = "Ж1" if art.endswith("-1") else ("Ж3" if art.endswith("-3") else "Ж2")
            ok = "✅" if zh == expected else "❌"
            print(f"  {ok} {art:15} Ж={zh:4}(жд={expected}) Д={str(dl):5} К={kat:20} Л={lin}")
    print()

    # ─── 8. FBM — все должны быть Ж2 ──────────────────────────────────────────
    print("=" * 70)
    print("8. FIBROTOP MAX — все должны быть Ж2")
    print("=" * 70)
    for p in active:
        art = p.get("article", "") or ""
        if art.startswith("FBM") and not art.startswith("FBMAX"):
            zh = attr(p, "Жесткость") or "—"
            dl = attr(p, "Длина, см") or "—"
            ok = "✅" if zh == "Ж2" else "❌"
            print(f"  {ok} {art:15} Ж={zh:4} Д={str(dl):5}")
    print()

    # ─── 9. D18H — проверка соответствия жёсткости артикулу ──────────────────
    print("=" * 70)
    print("9. МАТЫ ДЕТЕНЗОР D18H — проверка")
    print("=" * 70)
    expected_d18 = {
        "D18H00": "Ж0", "D18H01": "Ж1", "D18H02": "Ж2",
        "D18H02P": "Ж2+", "D18H03": "Ж3",
    }
    for p in active:
        art = p.get("article", "") or ""
        if art.startswith("D18H"):
            zh = attr(p, "Жесткость") or "—"
            dl = attr(p, "Длина, см") or "—"
            kat = attr(p, "Категория для аналитики") or "—"
            lin = attr(p, "Линейка") or "—"
            exp = expected_d18.get(art, "?")
            ok = "✅" if zh == exp else "❌"
            print(f"  {ok} {art:10} Ж={zh:4}(жд={exp}) Д={str(dl):5} К={kat:20} Л={lin}")
    print()

    # ─── 10. Старые «Устройство медицинское...» без артикула ──────────────────
    print("=" * 70)
    print("10. СТАРЫЕ КАРТОЧКИ «Устройство медицинское DETENSOR ...»")
    print("    (без доп.артикула — НЕ обработаны нашими скриптами)")
    print("=" * 70)
    old_um = []
    for p in active:
        if "Устройство медицинское" in p["name"]:
            old_um.append(p)
    print(f"Активных карточек 'Устройство медицинское': {len(old_um)}")
    for p in old_um:
        cat = attr(p, "Категория для аналитики") or "—"
        print(f"  art={p.get('article','')[:55]:55} | К={cat}")
    print()

    # Итог
    print("=" * 70)
    print("ИТОГ АУДИТА")
    print("=" * 70)
    print(f"  Исключено (Сырье/ТИМ):         {len(excluded)} — не проверяем")
    print(f"  В области проверки без Категории: {len(no_cat)}")
    print(f"  Модулей матов:                 {len(modules)}")
    print(f"  -NV вариантов:                 {len(nv)}")
    print(f"  Аренды в каталоге:             {len(rent)}")
    print(f"  Старых «Устройство мед.»:      {len(old_um)}")


if __name__ == "__main__":
    main()
