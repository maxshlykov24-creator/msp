"""Маппинг авто CME → 15 колонок Sheet1. Колонки не сдвигать (электро ≠ дырка)."""
from __future__ import annotations

from typing import Any, Iterable

HEADER: tuple[str, ...] = (
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
