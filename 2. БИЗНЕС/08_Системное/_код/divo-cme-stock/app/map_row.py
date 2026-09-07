"""Маппинг авто CME → лист «Данные».

A–O (CORE_HEADER) — контракт amo / FILTER на Sheet1. Порядок не менять.
P+ (EXTRA_HEADER) — расширение справа, amo их не читает.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CORE_HEADER: tuple[str, ...] = (
    "VIN",
    "Марка",
    "Модель",
    "Год выпуска",
    "Цвет",
    "Пробег",
    "Состояние авто",
    "Коробка передач",
    "Привод",
    "Объем двигателя",
    "Мощность двигателя",
    "Тип двигателя",
    "Количество владельцев по ПТС",
    "Комплектация",
    "Цена продажи",
)
EXTRA_HEADER: tuple[str, ...] = (
    "Поколение",
    "ПТС",
    "Учёт в РФ",
    "Без пробега РФ",
    "Автотека",
    "Окрасы",
    "Фото, шт",
    "Тип кузова",
)
HEADER: tuple[str, ...] = CORE_HEADER + EXTRA_HEADER


def col_letter(n: int) -> str:
    """1-based индекс колонки → A, B, …, Z, AA."""
    if n < 1:
        raise ValueError(n)
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


LAST_CORE_COL = col_letter(len(CORE_HEADER))  # O
LAST_COL = col_letter(len(HEADER))  # W

PTS_MAP = {
    "electron": "Электронный",
    "electronic": "Электронный",
    "эптс": "Электронный",
    "original": "Оригинал",
    "оригинал": "Оригинал",
    "duplicate": "Дубликат",
    "дубликат": "Дубликат",
}

AUTOTEKA_URL_RE = re.compile(r"https://(?:www\.)?autoteka\.ru/\S+", re.I)
PAINT_LINE_RE = re.compile(r"Покраска и работы:\s*([^\n]+)", re.I)

BODY_MAP = {
    "off": "кроссовер",
    "offroad": "кроссовер",
    "suv": "кроссовер",
    "sed": "седан",
    "sedan": "седан",
    "hatch": "хэтчбек",
    "hatchback": "хэтчбек",
    "liftback": "хэтчбек",
    "miniwan": "минивэн",
    "minivan": "минивэн",
    "mpv": "минивэн",
    "wag": "универсал",
    "wagon": "универсал",
    "coupe": "купе",
    "pickup": "пикап",
}

PAINT_PARTS = {
    "ппк": "переднее правое крыло",
    "ппд": "передняя правая дверь",
    "плк": "переднее левое крыло",
    "плд": "передняя левая дверь",
    "зпк": "заднее правое крыло",
    "зпд": "задняя правая дверь",
    "злд": "задняя левая дверь",
    "злк": "заднее левое крыло",
    "кб": "крышка багажника",
}

PAINT_CODE_RE = re.compile(r"\b(ппк|ппд|плк|плд|зпк|зпд|злд|злк|кб)\b", re.I)
PAINT_JUNK = (
    re.compile(r"расч[её]ты?\s+в\s+авт[оа]теке[^.!,;]*", re.I),
    re.compile(r"автотека\s+зелен\w*", re.I),
    re.compile(r"так\s+же\s+вопрос\s+по\s+\w+", re.I),
    re.compile(r"вопрос\s+по\s+\w+", re.I),
    re.compile(r"\(\s*\d+\s*[–-]\s*\d+\s*мкр\s*\)", re.I),
    re.compile(r"\d+\s*[–-]\s*\d+\s*мкр", re.I),
    re.compile(r"до\s+\d+\s*мкр", re.I),
    re.compile(r"в\s+районе\s+\d+\s*мкр", re.I),
    re.compile(r"локально\s+\d+\s*[–-]?\s*\d*\s*мкр", re.I),
    re.compile(r"\b\d+\s*мкр\b", re.I),
)

# Не generation («I Рестайлинг») — это не комплектация.
COMPLECTATION_KEYS: tuple[str, ...] = (
    "complectation",
    "complectationName",
    "equipment",
    "equipmentName",
    "modification",
    "modificationName",
    "trim",
    "configuration",
    "configurationName",
    "nameComplectation",
    "complectationTitle",
)

COLOR_MAP = {
    "black": "черный",
    "gray": "серый",
    "grey": "серый",
    "silver": "серебряный",
    "white": "белый",
    "beige": "бежевый",
    "yellow": "желтый",
    "gold": "золотой",
    "orange": "оранжевый",
    "brown": "коричневый",
    "red": "красный",
    "pink": "розовый",
    "violet": "фиолетовый",
    "purple": "пурпурный",
    "blue": "синий",
    "skyblue": "голубой",
    "green": "зеленый",
}

GEAR_MAP = {
    "mt": "Механика",
    "at": "Автомат",
    "cvt": "Вариатор",
    "amt": "Робот",
    "механика": "Механика",
    "механическая": "Механика",
    "автомат": "Автомат",
    "автоматическая": "Автомат",
    "вариатор": "Вариатор",
    "робот": "Робот",
}

DRIVE_MAP = {
    "rwd": "Задний",
    "fwd": "Передний",
    "awd": "Полный",
    "задний": "Задний",
    "передний": "Передний",
    "полный": "Полный",
}

ENGINE_MAP = {
    "petrol": "Бензин",
    "diesel": "Дизель",
    "electric": "Электро",
    "gas": "Газ",
    "hybrid": "Гибрид",
    "бензин": "Бензин",
    "дизель": "Дизель",
    "электро": "Электро",
    "газ": "Газ",
    "гибрид": "Гибрид",
}

STATE_MAP = {
    "excellent": "Отличное (A)",
    "good": "Хорошее (B)",
    "average": "Среднее (C)",
    "bad": "Плохое (D)",
    "broken": "Битый",
    "отличное": "Отличное (A)",
    "хорошее": "Хорошее (B)",
    "среднее": "Среднее (C)",
    "плохое": "Плохое (D)",
    "битый": "Битый",
    "a": "Отличное (A)",
    "b": "Хорошее (B)",
    "c": "Среднее (C)",
    "d": "Плохое (D)",
}


def pick(obj: Any, *paths: str) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok or cur in (None, ""):
            continue
        if isinstance(cur, dict):
            for k in ("name", "title", "value", "label"):
                if cur.get(k) not in (None, ""):
                    return cur[k]
            continue
        return cur
    return None


def _norm_key(value: Any) -> str:
    return str(value).strip().lower().replace("ё", "е")


def _map_lookup(value: Any, table: dict[str, str], *, title: bool = False) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    mapped = table.get(_norm_key(raw))
    if mapped:
        return mapped
    return raw.title() if title else raw


def format_int_spaces(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        n = int(round(float(str(value).replace(" ", "").replace(",", "."))))
    except (TypeError, ValueError):
        return str(value).strip()
    s = f"{n:,}".replace(",", " ")
    return s


def format_volume(value: Any, *, electric: bool) -> str:
    if electric:
        return ""
    if value in (None, ""):
        return ""
    try:
        n = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return str(value).strip()
    # литры, не «163 кВт» в колонке объёма
    if n > 20:
        return ""
    return f"{n:.2f}".replace(".", ",")


def format_power(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if _norm_key(text) in ENGINE_MAP:
        return ""
    return format_int_spaces(text)


def format_state(value: Any) -> str:
    if value in (None, ""):
        return "Не указано"
    raw = str(value).strip()
    key = _norm_key(raw)
    if key in STATE_MAP:
        return STATE_MAP[key]
    # уже «Отличное (A)»
    compact = key.replace(" ", "")
    for needle, label in (
        ("(a)", "Отличное (A)"),
        ("(b)", "Хорошее (B)"),
        ("(c)", "Среднее (C)"),
        ("(d)", "Плохое (D)"),
    ):
        if needle in compact:
            return label
    mapped = STATE_MAP.get(key.split()[0] if key else "")
    return mapped or raw


def format_color(value: Any) -> str:
    if value in (None, ""):
        return ""
    mapped = COLOR_MAP.get(_norm_key(value))
    if mapped:
        return mapped
    return str(value).strip().lower()


def format_engine(value: Any) -> str:
    return _map_lookup(value, ENGINE_MAP, title=True)


def complectation_of(car: dict[str, Any]) -> str:
    for key in COMPLECTATION_KEYS:
        val = pick(car, key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def vin_of(car: dict[str, Any]) -> str:
    vin = pick(car, "vin", "VIN", "Vin")
    return str(vin).strip().upper() if vin else ""


def format_pts(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    return PTS_MAP.get(_norm_key(raw), raw)


def format_yes_no(value: Any) -> str:
    if value is True:
        return "Да"
    if value is False:
        return "Нет"
    if value in (None, ""):
        return ""
    key = _norm_key(value)
    if key in {"true", "1", "да", "yes"}:
        return "Да"
    if key in {"false", "0", "нет", "no"}:
        return "Нет"
    return str(value).strip()


def autoteka_of(comment: str) -> str:
    if not comment:
        return ""
    m = AUTOTEKA_URL_RE.search(comment)
    if not m:
        return ""
    return m.group(0).rstrip(").,]\"'")


def format_body(value: Any) -> str:
    if value in (None, ""):
        return ""
    return BODY_MAP.get(_norm_key(value), "")


def _join_ru(parts: list[str]) -> str:
    uniq: list[str] = []
    for part in parts:
        if part and part not in uniq:
            uniq.append(part)
    if not uniq:
        return ""
    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) == 2:
        return "%s и %s" % (uniq[0], uniq[1])
    return "%s и %s" % (", ".join(uniq[:-1]), uniq[-1])


def paint_human(raw: str) -> str:
    """Складские коды и пометки → одна фраза для клиента."""
    if not raw:
        return ""
    text = raw.strip()
    for rx in PAINT_JUNK:
        text = rx.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:()")
    key = _norm_key(text)
    if key in {"нет", "-", "н/д", "нет окрасов"}:
        return "без окрасов"

    extras: list[str] = []
    no_paint = "без окрас" in key
    if "без дтп" in key:
        extras.append("без ДТП")
    if "зон" in key and "плен" in key:
        extras.append("зоны риска в плёнке")
    elif "броне" in key:
        extras.append("передняя часть в бронеплёнке")
    elif "плен" in key:
        extras.append("в плёнке")
    if "скол" in key:
        extras.append("скол на лобовом стекле" if "лобов" in key else "скол")

    parts: list[str] = []
    if "вся правая сторона" in key or "всю правую" in key:
        parts.append("вся правая сторона")
    if "вся левая сторона" in key:
        parts.append("вся левая сторона")
    if "крышк" in key and "багаж" in key:
        loc = "локальный окрас крышки багажника"
        if "лев" in key:
            loc += " слева"
        elif "прав" in key:
            loc += " справа"
        parts.append(loc)

    right_side = "вся правая сторона" in parts
    for match in PAINT_CODE_RE.finditer(key):
        name = PAINT_PARTS.get(match.group(1))
        if not name:
            continue
        if right_side and name.startswith("заднее правое"):
            continue
        if name not in parts:
            parts.append(name)

    if re.search(r"капот", key) and "капот" not in parts:
        parts.append("капот")

    if no_paint and not parts:
        return ", ".join(["без окрасов"] + extras)

    if parts:
        if len(parts) == 1 and parts[0] == "капот":
            head = "окрашен капот"
        elif len(parts) == 1 and parts[0].startswith("локальный"):
            head = parts[0]
        elif len(parts) == 1 and parts[0].startswith("вся "):
            head = "окрашена " + parts[0]
        else:
            head = "окрашены " + _join_ru(parts)
        bits = [head]
        if "ремонт" in key:
            bits.append("с ремонтом")
        bits.extend(extras)
        return ", ".join(bits)

    if no_paint:
        return ", ".join(["без окрасов"] + extras)
    return text


def paint_of(comment: str) -> str:
    m = PAINT_LINE_RE.search(comment or "")
    if not m:
        return ""
    raw = m.group(1).strip().rstrip(".")
    return paint_human(raw)


def photos_count(car: dict[str, Any]) -> str:
    n = pick(car, "photosAmount")
    if n not in (None, ""):
        try:
            return str(int(float(str(n))))
        except (TypeError, ValueError):
            pass
    urls = car.get("photosUrls") or car.get("photos") or []
    if isinstance(urls, list) and urls:
        return str(len(urls))
    return ""


def map_row(car: dict[str, Any]) -> list[str]:
    engine = format_engine(pick(car, "engine", "engineType", "engine.type"))
    electric = _norm_key(engine) == "электро"
    volume_raw = pick(car, "volume", "engineVolume", "engine.volume")
    power_raw = pick(car, "power", "enginePower", "engine.power")

    owners = pick(car, "ownersAmount", "owners", "ptsOwners")
    owners_s = ""
    if owners not in (None, ""):
        try:
            owners_s = str(int(float(str(owners).replace(" ", "").replace(",", "."))))
        except (TypeError, ValueError):
            owners_s = str(owners).strip()

    price = pick(car, "sellingPrice", "price", "salePrice")
    price_s = ""
    if price not in (None, ""):
        try:
            price_s = str(int(round(float(str(price).replace(" ", "").replace(",", ".")))))
        except (TypeError, ValueError):
            price_s = str(price).strip()

    year = pick(car, "year")
    year_s = ""
    if year not in (None, ""):
        try:
            year_s = str(int(float(str(year))))
        except (TypeError, ValueError):
            year_s = str(year).strip()

    comment = str(pick(car, "anyCommentDescription", "comment") or "")

    return [
        vin_of(car),
        str(pick(car, "brand", "brandName", "mark") or "").strip(),
        str(pick(car, "model", "modelName") or "").strip(),
        year_s,
        format_color(pick(car, "color", "colorName")),
        format_int_spaces(pick(car, "mileage", "km")),
        format_state(pick(car, "vehicleState", "state", "condition")),
        _map_lookup(pick(car, "gear", "gearType", "transmission"), GEAR_MAP, title=True),
        _map_lookup(pick(car, "drive", "driveType"), DRIVE_MAP, title=True),
        format_volume(volume_raw, electric=electric),
        format_power(power_raw),
        engine,
        owners_s,
        complectation_of(car),
        price_s,
        str(pick(car, "generation", "generationName") or "").strip(),
        format_pts(pick(car, "originalPts", "ptsType", "pts")),
        format_yes_no(pick(car, "registeredInRu")),
        format_yes_no(pick(car, "withoutMileageInRu")),
        autoteka_of(comment),
        paint_of(comment),
        photos_count(car),
        format_body(pick(car, "body", "bodyType")),
    ]


def dedup_sort(rows: Iterable[list[str]]) -> list[list[str]]:
    by_vin: dict[str, list[str]] = {}
    skipped = 0
    for row in rows:
        vin = (row[0] if row else "").strip().upper()
        if not vin:
            skipped += 1
            continue
        by_vin[vin] = row
    return [by_vin[k] for k in sorted(by_vin)]
