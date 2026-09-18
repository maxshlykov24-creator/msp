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

import json
import re
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
    "НДС", "История",
)
LAST_COL = "Y"  # ширина HEADER; шире листа не читаем
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
    values = read_values(svc, sheet, LAST_COL)
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


def read_autoteka() -> dict[str, dict[str, object]]:
    """Факты из отчётов автотеки: VIN -> факты. Кэш пишет tools/autoteka_sync.py."""
    path = settings.state_dir / "autoteka.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("stock_sync: автотека не прочитана: %s" % exc, file=sys.stderr)
        return {}
    out: dict[str, dict[str, object]] = {}
    for vin, entry in (data or {}).items():
        facts = (entry or {}).get("факты") if isinstance(entry, dict) else None
        if isinstance(facts, dict) and facts:
            out[str(vin).strip().upper()] = facts
    return out


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


NEW_CAR_KM = 500


def km_int(raw: str) -> int | None:
    return price_int(raw)


def is_new_import(d: dict) -> bool:
    """Свежий ввоз: истории ещё нет, отчёта автотеки физически нет."""
    km = km_int(d.get("Пробег") or "")
    rf = (d.get("Учёт в РФ") or "").strip().lower()
    fresh = (d.get("Без пробега РФ") or "").strip().lower()
    no_rf = rf in {"нет", "no"}
    no_miles = fresh in {"да", "yes"}
    if km is not None and km <= NEW_CAR_KM:
        return True
    return no_rf and no_miles


def autoteka_field(d: dict) -> str:
    """Ссылка, «нет потому что новая», или «ссылка не подшита».

    Голое «Автотека: нет» на машине с пробегом модель читает как «отчёта
    не существует». Вадим по Monjaro сам открыл отчёт. Ссылки нет в CME,
    отчёт у автотеки есть.
    """
    auto = (d.get("Автотека") or "").strip()
    if auto.startswith("http"):
        return auto
    if is_new_import(d):
        return "нет. Машина новая, отчёта ещё нет. Клиенту так и говори."
    return (
        "ссылка в карточке не стоит. Это не значит, что отчёта не существует. "
        "Клиенту нельзя говорить «автотеки нет» и «отчёта нет вообще». "
        "ДТП и окрасы наугад не называй: уточню, пришлю в Телеграм или Ватсап. "
        "Лизинг нашей машины: выкуплена, документы у нас, даже если в отчёте "
        "ещё светится срок."
    )


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


def vat_price(raw: str) -> str:
    """Цена на юрлицо: плюс 15%% и вверх до ровных 50 тысяч.

    Считаем здесь, а не в промпте: модель арифметику врёт, а цена - обязательство.
    """
    n = price_int(raw)
    if not n:
        return ""
    step = 50_000
    total = -(-int(round(n * 1.15)) // step) * step
    return "%s руб." % format(total, ",").replace(",", " ")


AUTOTEKA_ORDER = (
    "такси",
    "каршеринг",
    "повреждения",
    "риски",
    "владельцы",
    "регион регистрации",
)


def is_nat(d: dict) -> bool:
    blob = " ".join((d.get("Марка") or "", d.get("Модель") or "")).lower()
    return "nat" in blob


def is_empty_phrase(text: str) -> bool:
    """«Не найдено» и «не проверено» в карточку не пускаем.

    Модель из «разрешений не найдено» делает «по базам чисто», а отсутствие
    записи в реестре ничего не доказывает. Пусто честнее.
    """
    low = str(text or "").lower()
    return any(m in low for m in ("не найден", "не обнаруж", "не проверен", "нет сведени"))


EMPTY_DAMAGE = re.compile(
    r"^(по отч[её]ту\s+)?"
    r"дтп"
    r"([,\s]+страховых выплат)?"
    r"([,\s]+и?\s*кузовного ремонта)?"
    r"\s*(нет|не найдено)$",
    re.I,
)
REAL_DAMAGE = re.compile(
    r"(одно\s+дтп|\d+\s*дтп|есть записи|есть страховые|"
    r"\d{1,2}\s+[а-яё]+\s+\d{4})",
    re.I,
)


def speak_damage(text: str) -> str:
    """Пустой отчёт голосом менеджера, без запятой после «ДТП».

    Старый кэш: «по отчёту ДТП, страховых выплат, кузовного ремонта не найдено».
    После склейки получалось «ДТП, страховых выплат и кузовного ремонта нет».
    Модель читает запятую как «ДТП есть». Так уже соврали по Bestune NAT.
    """
    t = str(text or "").strip()
    if not t:
        return ""
    compact = re.sub(r"\s+", " ", t.lower()).strip(" .")
    compact = compact.replace(" не найдены", " не найдено")
    if EMPTY_DAMAGE.search(compact) and not REAL_DAMAGE.search(compact):
        return "ДТП не было, кузов не чинили"
    low = t.lower()
    for prefix in ("по отчёту ", "по отчету "):
        if low.startswith(prefix):
            t = t[len(prefix) :]
            low = t.lower()
            break
    t = t.replace(" не найдено", " нет").replace(" не найдены", " нет")
    head, sep, tail = t.rpartition(" нет")
    if sep and "," in head and " и " not in head:
        parts = [p.strip() for p in head.split(",") if p.strip()]
        if len(parts) >= 2:
            t = "%s и %s нет" % (", ".join(parts[:-1]), parts[-1])
    return t


OWNER_COUNT = re.compile(r"\d+\s+владел\w+", re.I)


def speak_owners(text: str) -> str:
    """Клиенту только число владельцев: без юрлица и лекции про износ.

    Автотека кладёт в title «1 владелец», а в alerts — «владело юридическое
    лицо» и «износ выше». Это не про продажу на организацию, в чат не несём.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    found = OWNER_COUNT.search(raw)
    return found.group(0) if found else ""


BODY_PART = re.compile(
    r"\b("
    r"крыл\w*|двер\w*|капот\w*|бампер\w*|порог\w*|"
    r"крышк\w*|крыш[аеиу]\w*|зеркал\w*|лонжерон\w*|стойк\w*|арк\w*"
    r")\b",
    re.I,
)
MANY_PAINTS = 3


def paint_part_count(text: str) -> int:
    return len(BODY_PART.findall(str(text or "")))


def speak_paints(text: str) -> str:
    """Много окрасов в карточке не держим списком: модель его копирует в чат.

    Один-два элемента оставляем как есть. Три и больше - «несколько
    косметических», без крыльев и дверей. Мусор CME («12. Автотека») режем.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if re.match(r"^\d+\.", raw):
        return ""
    low = raw.lower()
    if paint_part_count(raw) < MANY_PAINTS:
        return raw
    out = "несколько косметических окрасов"
    if "плёнк" in low or "пленк" in low:
        out += ", в плёнке"
    return out


def autoteka_lines(facts: dict[str, object] | None) -> list[str]:
    """Факты отчёта - отдельным блоком, чтобы бот не путал их со своей базой."""
    if not facts:
        return []
    out = ["- По отчёту автотеки:"]
    for key in AUTOTEKA_ORDER:
        value = facts.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = "; ".join(str(x) for x in value if x and not is_empty_phrase(str(x)))
        if not value:
            continue
        # Пустые «разрешений не найдено» в карточку не пускаем. ДТП - да:
        # сводят из ГИБДД, страховых и СТО, клиенту надо ответить.
        if key != "повреждения" and is_empty_phrase(str(value)):
            continue
        if key == "повреждения":
            value = speak_damage(str(value))
            if not value:
                continue
        if key == "владельцы":
            value = speak_owners(str(value))
            if not value:
                continue
        out.append("  - %s: %s" % (key, value))
    return out if len(out) > 1 else []


def card(
    row: list[str],
    marks: dict[str, str] | None = None,
    autoteka: dict[str, dict[str, object]] | None = None,
) -> str:
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
    if is_nat(d):
        lines.append(
            "- Дизельный отопитель: да. Модельный факт FAW Bestune NAT. "
            "Клиенту говори только если спросил про отопитель, нагреватель или вебасто: "
            "да, дизельный отопитель стоит. Сам строку в чат не тащи. "
            "Не писать «в описании нет» и не просить номер из-за этого вопроса."
        )
    add("Поколение", d.get("Поколение", ""))
    add("ПТС", d.get("ПТС", ""))
    add("Учёт в РФ", d.get("Учёт в РФ", ""))
    add("Без пробега РФ", d.get("Без пробега РФ", ""))
    add("Окрасы", speak_paints(d.get("Окрасы", "")))
    add("Автотека", autoteka_field(d))
    if not price_int(d.get("Цена продажи") or ""):
        lines.append(
            "- В карточке цены продажи нет. Машина у нас. Пишет с объявления, где цена есть, "
            "называй цену объявления. Клиенту не говори, что в базе цены нет."
        )
    photos = d.get("Фото, шт", "")
    if photos:
        lines.append("- Фото в объявлении: %s" % photos)
    history = (d.get("История") or "").strip()
    if history:
        lines.append("- История эксплуатации: %s" % history)
    else:
        # Пустая строка провоцирует «по базе чисто», поэтому запрет пишем прямо
        # в карточку: отсутствие записи это не доказательство.
        lines.append(
            "- История эксплуатации: данных нет. Не писать «не была в такси»,"
            " «не каршеринг», «чистая по базам» - только «уточню у менеджера»"
        )
    lines.append("- Лига: %s" % league(d["Марка"]))
    lines.append("- Тип: %s" % body(d["Модель"], d.get("Тип кузова", "")))
    lines.append("- Цена в объявлении: %s (наличный расчет, без НДС)" % money(d["Цена продажи"]))
    vat_total = vat_price(d["Цена продажи"])
    if vat_total:
        lines.append("- Цена на юрлицо с НДС: %s (расчетный счет)" % vat_total)
    else:
        lines.append(
            "- Готовой цены на юрлицо нет, цифру с НДС не выдумывай. "
            "Если в объявлении цена есть - считай от неё, иначе уточни"
        )
    vin_key = (d["VIN"] or "").strip().upper()
    lines += autoteka_lines((autoteka or {}).get(vin_key))
    note = (marks or {}).get(vin_key, "")
    if note:
        lines.append("- **Важно: %s**" % note)
    return "\n".join(lines)


def dedup_by_vin(rows: list[list[str]]) -> list[list[str]]:
    """Один VIN - одна карточка: лист «Склад» может пересечься с «Данные»."""
    by_vin: dict[str, list[str]] = {}
    for row in rows:
        vin = (row[0] or "").strip().upper()
        if vin:
            by_vin.setdefault(vin, row)
    return [by_vin[k] for k in sorted(by_vin)]


def split_by_price(rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """Склад делим по цене: цена стоит - машина продаётся, нет - к менеджеру.

    Статусы продажи в CME менеджеры ведут не всегда: Cullinan висел offsale с
    ценой 37 млн и при этом реально продавался. Цена - признак честнее статуса.
    """
    priced: list[list[str]] = []
    rest: list[list[str]] = []
    for row in rows:
        d = dict(zip(HEADER, row))
        (priced if price_int(d["Цена продажи"]) else rest).append(row)
    return priced, rest


def warehouse_block(
    rows: list[list[str]],
    marks: dict[str, str] | None = None,
    autoteka: dict[str, dict[str, object]] | None = None,
) -> str:
    """Полные карточки: на складе есть, но цены в CME нет.

    Короткий список без VIN оставлял бота без ДТП и автотеки: клиент пишет
    с объявления, а модель отвечает «карточки нет, сверяю по стоку».
    """
    if not rows:
        return ""
    out = [
        "",
        "# На складе, в карточке нет цены продажи",
        "",
        "> Эти машины физически у нас. VIN, автотека, ДТП и окрасы в карточках",
        "> ниже — отвечай по ним как по любой машине из стока.",
        "> Цены продажи в карточке нет: если клиент пишет с объявления, бери цену",
        "> объявления и не говори, что «в базе цены нет». Спросил цену, а в",
        "> объявлении её нет — машина есть, цену уточни, возьми контакт.",
        "> Не говори «в продаже нет» и «продана». В подбор по бюджету не предлагай.",
        "",
    ]
    cards = [
        card(r, marks, autoteka)
        for r in sorted(rows, key=lambda x: (x[1], x[2], x[3]))
    ]
    return "\n".join(out) + "\n\n".join(cards) + "\n"


def build_stock(
    rows: list[list[str]],
    stamp: str,
    marks: dict[str, str] | None = None,
    warehouse: list[list[str]] | None = None,
    autoteka: dict[str, dict[str, object]] | None = None,
) -> str:
    head = [
        "# Сток DIVO Motors — машины в наличии",
        "",
        "> Обновлено: %s. Источник: таблица «Автомобили | DIVO MOTORS»," % stamp,
        "> листы «Данные» и «Склад»: всё, что физически на складе и с ценой.",
        ">",
        "> **Машин в продаже: %d.** Ниже есть второй, короткий список: машины на" % len(rows),
        "> складе без цены. Чего нет ни там, ни там — того у нас нет.",
        "> Не называть VIN, цену и комплектацию, которых здесь нет.",
        "> «Цена на юрлицо с НДС» уже посчитана — бери строкой, сам не умножай.",
        "> Формула одна на все машины, флаг НДС для цифры не нужен. Нет цены ни в",
        "> карточке, ни в объявлении — тогда «уточню», а не «нельзя».",
        "> «История эксплуатации» — такси или каршеринг по нашей базе. Строки нет —",
        "> данных нет: не отрицать, что машина была в такси, а звать менеджера.",
        "> Блок «По отчёту автотеки» — то, что клиент увидит по ссылке сам.",
        "> В нём только найденное. Пустой блок или отсутствие строки — «данных нет»,",
        "> а не «чисто»: «в такси не работала», «каршеринга не было», «по базам",
        "> чисто» не писать никогда. Каршеринг в отчёт почти не попадает, поэтому",
        "> наша «История эксплуатации» сильнее отчёта.",
        "> В карточке нет поля — значит данных нет. Не додумывать и не выводить из",
        "> соседних полей: пустые «Окрасы» это «нет данных», а не «заводской окрас».",
        "> Запаса хода, расхода и разгона здесь нет вообще — наугад их не называть.",
        "> Строка «Важно» в карточке — пометка менеджеров, она сильнее общих правил.",
        "> Исключение — строка «повреждения»: её называешь клиенту, потому что",
        "> ДТП сводят из ГИБДД, страховых и СТО. Ты менеджер этой машины: «ДТП не",
        "> было, кузов не чинили». Канцелярит «по отчёту не найдено» в чат не идёт.",
        "> Слоганы «машина не битая» и «чистая по базам» тоже не нужны.",
        "> Наши машины выкуплены, документы у нас. Строка автотеки «в лизинге до»",
        "> это реестр, не текущий статус. Спросили «она в лизинге?» про нашу машину:",
        "> «нет, выкуплена, документы на руках». Если в отчёте ещё светится срок,",
        "> «в базах может отображаться, по факту закрыт, на сделке покажем».",
        "> Ссылку автотеки кидай в чат только если клиент просит отчёт, историю или ДТП,",
        "> машина уже понятна и в карточке есть URL. Сам в первом сообщении не кидай.",
        "> «Автотека: нет. Машина новая» значит отчёта правда нет.",
        "> «ссылка в карточке не стоит» значит не говори клиенту, что автотеки не существует.",
        "",
    ]
    body_text = "\n\n".join(card(r, marks, autoteka) for r in rows) + "\n"
    return "\n".join(head) + body_text + warehouse_block(warehouse or [], marks, autoteka)


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

    by_engine: dict[str, list[list[str]]] = {}
    for r in rows:
        eng = (r[11] or "не указан").strip() or "не указан"
        by_engine.setdefault(eng, []).append(r)

    out += [
        "",
        "## По двигателю",
        "",
        "«Электричка» в чате про машины = электромобиль, не поезд до салона.",
        "Подбор по полю «Двигатель», тип кузова не режет.",
        "",
    ]
    for eng in sorted(by_engine, key=lambda x: (x == "не указан", x.lower())):
        cars = sorted(by_engine[eng], key=lambda c: price_int(c[14]) or 10**12)
        bits = []
        for c in cars:
            bits.append(
                "%s %s %s"
                % (c[1], c[2], money(c[14]))
            )
        out.append("- **%s** (%d): %s" % (eng, len(cars), "; ".join(bits)))

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
    autoteka = read_autoteka()
    priced, no_price = split_by_price(read_warehouse(svc))
    on_sale = dedup_by_vin(rows + priced)
    stamp = datetime.now(MSK).strftime("%Y-%m-%d %H:%M МСК")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "СТОК.md").write_text(
        build_stock(on_sale, stamp, marks, no_price, autoteka), encoding="utf-8"
    )
    (out_dir / "_ИНДЕКС.md").write_text(build_index(on_sale, stamp), encoding="utf-8")
    if not quiet:
        print(
            "stock_sync: %d с ценой (%d из «Данные», %d со «Склад»), "
            "%d на складе без цены, %d пометок, %d отчётов автотеки, обновлено %s"
            % (
                len(on_sale),
                len(rows),
                len(priced),
                len(no_price),
                len(marks),
                len(autoteka),
                stamp,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
