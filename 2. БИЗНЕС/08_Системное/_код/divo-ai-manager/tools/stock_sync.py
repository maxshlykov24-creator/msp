#!/usr/bin/env python3
"""Сток CM Expert -> база знаний агента.

Источник: таблица «Автомобили | DIVO MOTORS», три листа.
    «Данные»  — машины в продаже, их пишет divo-cme-stock каждые 15 минут:
                CME API -> фильтр «на складе + в продаже + на площадке» ->
                A–O как у amo, плюс поля справа.
    «Склад»   — на складе, но не в продаже. Пишет тот же воркер.
    «Пометки» — ручные заметки менеджеров по VIN. Синк их только читает.

Здесь только чтение и раскладка в markdown под grep:
    workspace/KB/сток/СТОК.md      — по одной карточке на машину
    workspace/KB/сток/_ИНДЕКС.md   — сводка по маркам, цены, дата обновления

Запуск: tools/stock_sync.py [--quiet]
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import settings  # noqa: E402

HEADER = (
    "VIN", "Марка", "Модель", "Год выпуска", "Цвет", "Пробег",
    "Состояние авто", "Коробка передач", "Привод", "Объем двигателя",
    "Мощность двигателя", "Тип двигателя", "Количество владельцев по ПТС",
    "Комплектация", "Цена продажи",
    "Поколение", "ПТС", "Учёт в РФ", "Без пробега РФ",
    "Автотека", "Окрасы", "Фото, шт", "Тип кузова",
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
MSK = timezone(timedelta(hours=3))


def sheets_api():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_path = settings.google_sa_path
    if not sa_path or not Path(sa_path).exists():
        raise RuntimeError(
            "не найден google_sa.json; укажи GOOGLE_SA_PATH в .env"
        )
    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def read_values(svc, sheet: str, last_col: str) -> list[list[str]]:
    rng = "'%s'!A1:%s" % (sheet.replace("'", "''"), last_col)
    return (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=settings.spreadsheet_id, range=rng)
        .execute()
        .get("values", [])
    )


def read_cars(svc, sheet: str) -> list[list[str]]:
    """Строки листа с машинами, выровненные по ширине HEADER."""
    values = read_values(svc, sheet, "W")
    rows = [r for r in values[1:] if r and (r[0] or "").strip()]
    return [[(r[i] if i < len(r) else "").strip() for i in range(len(HEADER))] for r in rows]


def read_marks(svc) -> dict[str, str]:
    """Ручные пометки менеджеров: VIN -> текст. Лист заводится руками, его нет — ну и ладно."""
    try:
        values = read_values(svc, settings.marks_sheet, "C")
    except Exception as exc:  # noqa: BLE001 — нет листа или прав, работаем без пометок
        print("stock_sync: пометки не прочитаны: %s" % exc, file=sys.stderr)
        return {}
    marks: dict[str, str] = {}
    for row in values[1:]:
        vin = (row[0] if row else "").strip().upper()
        note = (row[2] if len(row) > 2 else "").strip()
        if vin and note:
            marks[vin] = note
    return marks


def read_warehouse(svc) -> list[list[str]]:
    """Машины на складе, но не в продаже. Лист пишет divo-cme-stock."""
    try:
        return read_cars(svc, settings.warehouse_sheet)
    except Exception as exc:  # noqa: BLE001 — лист может ещё не появиться
        print("stock_sync: склад не прочитан: %s" % exc, file=sys.stderr)
        return []


def price_int(raw: str) -> int | None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else None


PREMIUM = {
    "porsche", "mercedes-benz", "mercedes", "bmw", "audi", "land rover",
    "range rover", "rolls-royce", "bentley", "aurus", "maserati", "jaguar",
}
STRONG = {
    "toyota", "lexus", "volkswagen", "skoda", "nissan", "honda", "mazda",
    "subaru", "hyundai", "kia", "volvo", "infiniti", "dodge", "ram", "jeep",
}
CHINA = {
    "geely", "haval", "chery", "exeed", "gac", "faw", "hongqi", "tank",
    "skywell", "changan", "omoda", "jaecoo", "belgee", "lixiang", "zeekr",
    "voyah", "great wall",
}
SUV = {
    "cayenne", "durango", "gle", "gle coupe", "gle coupe amg", "gls",
    "maybach gls", "g-класс", "g-класс amg", "monjaro", "tugella", "coolray",
    "h3", "h9", "jolion", "x-trail", "lx", "rx", "tucson", "gs8", "x5", "x6",
    "x7", "sportage", "santa fe", "rav4", "outlander",
}
SEDAN = {
    "panamera", "camry", "octavia", "b70", "bestune b70", "h5", "bora",
    "arrizo 8", "e-класс", "s-класс", "senat", "granta",
}
MPV = {"m8", "v-класс", "alphard", "carnival"}
PICKUP = {"1500", "amarok", "hilux"}


def league(brand: str) -> str:
    key = (brand or "").strip().lower()
    if key in PREMIUM:
        return "премиум"
    if key in STRONG:
        return "сильный масс"
    if key in {"lada", "lada (ваз)", "ваз"}:
        return "бюджет"
    if key in CHINA:
        return "китай"
    return "сильный масс"


def body(model: str, cme: str = "") -> str:
    if (cme or "").strip():
        return cme.strip()
    key = (model or "").strip().lower()
    if key in SUV or any(key.startswith(x) for x in ("gle", "gls", "g-класс")):
        return "кроссовер"
    if key in SEDAN:
        return "седан"
    if key in MPV:
        return "минивэн"
    if key in PICKUP:
        return "пикап"
    return "кроссовер"


def money(raw: str) -> str:
    n = price_int(raw)
    return "%s руб." % format(n, ",").replace(",", " ") if n else "не указана"


def card(row: list[str], marks: dict[str, str] | None = None) -> str:
    d = dict(zip(HEADER, row))
    title = " ".join(x for x in (d["Марка"], d["Модель"], d["Год выпуска"]) if x)
    lines = ["## %s" % (title or d["VIN"])]

    def add(label: str, value: str, suffix: str = "") -> None:
        if value:
            lines.append("- %s: %s%s" % (label, value, suffix))

    add("VIN", d["VIN"])
    add("Марка", d["Марка"])
    add("Модель", d["Модель"])
    add("Год", d["Год выпуска"])
    add("Пробег", d["Пробег"], " км")
    add("Цвет", d["Цвет"])
    add("Состояние", d["Состояние авто"])
    add("Коробка", d["Коробка передач"])
    add("Привод", d["Привод"])
    add("Двигатель", d["Тип двигателя"])
    add("Объем", d["Объем двигателя"], " л")
    add("Мощность", d["Мощность двигателя"], " л.с.")
    add("Владельцев по ПТС", d["Количество владельцев по ПТС"])
    add("Комплектация", d["Комплектация"])
    add("Поколение", d.get("Поколение", ""))
    add("ПТС", d.get("ПТС", ""))
    add("Учёт в РФ", d.get("Учёт в РФ", ""))
    add("Без пробега РФ", d.get("Без пробега РФ", ""))
    add("Окрасы", d.get("Окрасы", ""))
    auto = (d.get("Автотека") or "").strip()
    if auto.startswith("http"):
        add("Автотека", auto)
    else:
        lines.append("- Автотека: нет")
    photos = d.get("Фото, шт", "")
    if photos:
        lines.append("- Фото в объявлении: %s" % photos)
    lines.append("- Лига: %s" % league(d["Марка"]))
    lines.append("- Тип: %s" % body(d["Модель"], d.get("Тип кузова", "")))
    lines.append("- Цена в объявлении: %s (наличный расчет, без НДС)" % money(d["Цена продажи"]))
    note = (marks or {}).get((d["VIN"] or "").strip().upper(), "")
    if note:
        lines.append("- **Важно: %s**" % note)
    return "\n".join(lines)


def warehouse_block(rows: list[list[str]]) -> str:
    """Короткий список: есть на складе, но не в продаже. Цену не показываем."""
    if not rows:
        return ""
    out = [
        "",
        "## На складе, но не в продаже",
        "",
        "> Эти машины физически у нас, но в продажу не выставлены: цены нет,",
        "> сроков нет, продать их сейчас нельзя. Клиент спросил про такую -",
        "> скажи «в продаже сейчас нет, по ней уточнит менеджер» и возьми номер.",
        "> Причину не выдумывай, цену не называй, в подбор их не предлагай.",
        "",
    ]
    for r in sorted(rows, key=lambda x: (x[1], x[2], x[3])):
        d = dict(zip(HEADER, r))
        bits = [x for x in (d["Марка"], d["Модель"], d["Год выпуска"]) if x]
        tail = ", ".join(x for x in (d["Цвет"], "%s км" % d["Пробег"] if d["Пробег"] else "") if x)
        out.append("- %s%s" % (" ".join(bits), " (%s)" % tail if tail else ""))
    return "\n".join(out) + "\n"


def build_stock(
    rows: list[list[str]],
    stamp: str,
    marks: dict[str, str] | None = None,
    warehouse: list[list[str]] | None = None,
) -> str:
    head = [
        "# Сток DIVO Motors — машины в наличии",
        "",
        "> Обновлено: %s. Источник: CM Expert через таблицу «Автомобили | DIVO MOTORS»," % stamp,
        "> лист «Данные». Только машины на складе, в продаже и опубликованные на площадках.",
        ">",
        "> **Машин в продаже: %d.** Чего нет в этом списке — того в продаже нет." % len(rows),
        "> Не называть VIN, цену и комплектацию, которых здесь нет.",
        "> В карточке нет поля — значит данных нет. Не додумывать и не выводить из",
        "> соседних полей: пустые «Окрасы» это «нет данных», а не «заводской окрас».",
        "> Запаса хода, расхода и разгона здесь нет вообще — наугад их не называть.",
        "> Строка «Важно» в карточке — пометка менеджеров, она сильнее общих правил.",
        "> Ссылку автотеки кидай в чат только если клиент просит отчёт, историю или ДТП,",
        "> машина уже понятна и в карточке есть URL. Сам в первом сообщении не кидай.",
        "> Если в карточке «Автотека: нет» — не пиши, что отчёт есть.",
        "",
    ]
    body_text = "\n\n".join(card(r, marks) for r in rows) + "\n"
    return "\n".join(head) + body_text + warehouse_block(warehouse or [])


def build_index(rows: list[list[str]], stamp: str) -> str:
    by_brand: dict[str, list[list[str]]] = {}
    for r in rows:
        by_brand.setdefault(r[1] or "без марки", []).append(r)

    prices = [p for p in (price_int(r[14]) for r in rows) if p]
    out = [
        "# Индекс стока — что вообще есть",
        "",
        "> Обновлено: %s. Всего машин: %d." % (stamp, len(rows)),
        "",
        "## По маркам",
        "",
    ]
    for brand in sorted(by_brand):
        cars = by_brand[brand]
        models = sorted({(c[2] or "?") for c in cars})
        out.append("- **%s** (%d): %s" % (brand, len(cars), ", ".join(models)))

    out += [
        "",
        "## Лиги для подбора",
        "",
        "- премиум: Porsche, Mercedes, BMW и рядом с ними",
        "- сильный масс: Toyota, VW, Skoda, Nissan, Dodge",
        "- китай: Geely, Haval, Exeed, GAC, FAW, Hongqi",
        "- бюджет: Lada",
        "- Подмена: та же марка → та же лига и тот же тип → цена 70-130%.",
        "  Премиум на китай не менять.",
        "",
        "## Цены",
        "",
    ]
    if prices:
        out.append("- Дешевле всего: %s руб." % format(min(prices), ",").replace(",", " "))
        out.append("- Дороже всего: %s руб." % format(max(prices), ",").replace(",", " "))
    else:
        out.append("- Цены в листе не заполнены.")

    out += ["", "## Полный список одной строкой на машину", ""]
    for r in sorted(rows, key=lambda x: (x[1], x[2], x[3])):
        out.append(
            "- %s %s %s, %s км, %s — %s [VIN %s]"
            % (r[1], r[2], r[3], r[5] or "?", r[4] or "цвет не указан", money(r[14]), r[0])
        )
    return "\n".join(out) + "\n"


def main() -> int:
    quiet = "--quiet" in sys.argv
    out_dir = ROOT / "workspace" / "KB" / "сток"
    try:
        svc = sheets_api()
        rows = read_cars(svc, settings.data_sheet)
        if not rows:
            raise RuntimeError("в листе «%s» нет машин" % settings.data_sheet)
    except Exception as exc:  # noqa: BLE001 — сток не должен ронять бота
        print("stock_sync: пропуск, файлы не тронуты: %s" % exc, file=sys.stderr)
        return 1

    marks = read_marks(svc)
    warehouse = read_warehouse(svc)
    stamp = datetime.now(MSK).strftime("%Y-%m-%d %H:%M МСК")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "СТОК.md").write_text(
        build_stock(rows, stamp, marks, warehouse), encoding="utf-8"
    )
    (out_dir / "_ИНДЕКС.md").write_text(build_index(rows, stamp), encoding="utf-8")
    if not quiet:
        print(
            "stock_sync: %d в продаже, %d на складе не в продаже, %d пометок, обновлено %s"
            % (len(rows), len(warehouse), len(marks), stamp)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
