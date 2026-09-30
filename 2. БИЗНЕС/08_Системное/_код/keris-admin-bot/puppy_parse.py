"""Дата и цена щенка в том виде, как их пишут в чат."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
PRICE_MIN = 100_000
PRICE_MAX = 999_999

_GROUPED = re.compile(r"^\d{1,3}([.\s]\d{3})+$")
_DOT_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def current_year(now: datetime | None = None) -> int:
    moment = now or datetime.now(MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK)
    return moment.astimezone(MSK).year


def parse_birth(raw: str, *, year: int | None = None) -> tuple[str, str] | None:
    """`2.08` в текущем году. Год в чат не пишут."""
    match = _DOT_DATE.fullmatch((raw or "").strip())
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    born_year = current_year() if year is None else year
    try:
        datetime(born_year, month, day)
    except ValueError:
        return None
    iso = f"{born_year:04d}-{month:02d}-{day:02d}"
    label = f"{day}.{month:02d}"
    return iso, label


def parse_price(raw: str) -> int | None:
    """100 000, 100.000, 350000 и короткое 350 (тысячи).

    Щенок стоит от 100 тысяч до 999 тысяч. 10 000 и миллион не принимаем.
    """
    text = (raw or "").replace("\u00a0", " ").replace("₽", "").replace("руб.", "").strip().lower()
    text = text.replace(" ", "").replace(",", ".")
    if not text:
        return None
    if _GROUPED.fullmatch(text):
        number = int(text.replace(".", "").replace(" ", ""))
    elif text.isdigit():
        number = int(text)
        if number < PRICE_MIN and 100 <= number <= 999:
            number *= 1000
    else:
        return None
    if number < PRICE_MIN or number > PRICE_MAX:
        return None
    return number


def price_label(value: int | None) -> str:
    if value is None:
        return "не указана"
    return f"{value:,}".replace(",", " ")
