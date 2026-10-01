"""Связка объявления Авито с карточкой стока."""
from __future__ import annotations

import re

from bot.config import settings

YEAR_RE = re.compile(r"\b(20\d{2})\b")
KM_RE = re.compile(r"([\d\s\u00a0]+)\s*км", re.I)
NOISE_TOKENS = {
    "cvt", "at", "amt", "mt", "dsg", "робот", "автомат", "механика",
    "4wd", "awd", "2wd", "fwd",
}
# «G-класс» и «V-класс» без склейки теряют букву: оба становятся «класс»,
# и V-класс на Авито получает автотеку гелика. Так уже отправили Ильшату.
CLASS_RE = re.compile(r"\b([a-zа-я])[\s\-]*класс", re.I)
GENERIC = {
    "mercedes", "benz", "bmw", "audi", "toyota", "geely", "haval",
    "класс", "amg",
}
SKIP_TITLES = (
    "на складе",
    "по маркам",
    "по двигателю",
    "лиги",
    "цены",
    "полный список",
)


def digits(raw: str) -> int:
    n = re.sub(r"\D", "", raw or "")
    return int(n) if n else 0


def parse_listing(title: str, price_string: str = "") -> dict:
    title = title or ""
    year = YEAR_RE.search(title)
    km = KM_RE.search(title)
    price = digits(price_string)
    head = title.split(",")[0].strip()
    return {
        "title": title,
        "head": head,
        "year": int(year.group(1)) if year else 0,
        "km": digits(km.group(1)) if km else 0,
        "price": price,
    }


def _cards(stock: str) -> list[dict]:
    out: list[dict] = []
    chunks = re.split(r"(?m)^## ", stock)
    for chunk in chunks[1:]:
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        low = title.lower()
        if any(low.startswith(skip) for skip in SKIP_TITLES):
            continue
        fields = {"title": title, "raw": "## " + chunk.strip()}
        for line in lines[1:]:
            if line.startswith("- ") and ":" in line:
                key, val = line[2:].split(":", 1)
                fields[key.strip()] = val.strip()
        if not fields.get("VIN") and not fields.get("Марка"):
            continue
        fields["price"] = digits(fields.get("Цена в объявлении", ""))
        fields["year"] = digits(YEAR_RE.search(title).group(1) if YEAR_RE.search(title) else "")
        fields["km"] = digits(fields.get("Пробег", ""))
        out.append(fields)
    return out


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = CLASS_RE.sub(lambda m: m.group(1).lower() + "класс", t)
    # «2.0» иначе распадается в «2» и «0» и ломает «GAC M8 2.0 AT».
    t = re.sub(r"(\d)[.,](\d)", r"\1dot\2", t)
    return re.sub(r"[^a-zа-я0-9]+", " ", t).strip()


def _keep_token(token: str) -> bool:
    if token in NOISE_TOKENS:
        return False
    if re.fullmatch(r"\d+(dot\d+)?", token):
        return False
    # M8, X5, H9 — модели из двух символов, обычный порог «>2» их выкидывает.
    if len(token) > 2:
        return True
    return bool(re.fullmatch(r"[a-zа-я]+\d+", token))


def _core(text: str) -> str:
    return " ".join(t for t in _norm(text).split() if _keep_token(t))


def _class_code(text: str) -> str:
    m = re.search(r"\b([a-zа-я])класс\b", _norm(text))
    return m.group(1) if m else ""


def score(listing: dict, card: dict) -> int:
    points = 0
    head = _core(listing.get("head") or listing.get("title") or "")
    title = _core(card.get("title") or "")
    if not head or not title:
        return 0
    listing_class = _class_code(listing.get("head") or listing.get("title") or "")
    card_class = _class_code(card.get("title") or "")
    if listing_class and listing_class != card_class:
        return 0
    if head and head in title:
        points += 8
    elif title and title in head:
        points += 8
    tokens = [t for t in head.split() if _keep_token(t)]
    hit = sum(1 for t in tokens if t in title)
    points += hit * 2
    must = [t for t in tokens if t not in GENERIC]
    if must and not all(t in title for t in must):
        return 0
    if listing.get("year") and listing["year"] == card.get("year"):
        points += 4
    lp, cp = listing.get("price") or 0, card.get("price") or 0
    if lp and cp and min(lp, cp) > 0 and max(lp, cp) >= 2 * min(lp, cp):
        return 0
    if lp and cp and abs(lp - cp) <= 1000:
        points += 6
    lk, ck = listing.get("km") or 0, card.get("km") or 0
    if lk and ck and abs(lk - ck) <= 15_000:
        points += 3
    # Год на два и больше или пробег далеко: это другая машина.
    # Год модели и год выпуска могут разойтись на один, если пробег тот же.
    ly, cy = int(listing.get("year") or 0), int(card.get("year") or 0)
    if ly and cy and abs(ly - cy) >= 2:
        return 0
    if lk and ck and abs(lk - ck) > 20_000:
        return 0
    return points


def _stock_text(stock: str = "") -> str:
    if stock:
        return stock
    path = settings.kb / "сток" / "СТОК.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def fitting_cards(title: str, price_string: str = "", stock: str = "") -> list[dict]:
    """Карточки, которые проходят порог по этому объявлению. Не по реплике клиента."""
    listing = parse_listing(title, price_string)
    found = [c for c in _cards(_stock_text(stock)) if score(listing, c) >= 8]
    found.sort(key=lambda c: score(listing, c), reverse=True)
    return found


def match_card(title: str, price_string: str = "", stock: str = "") -> dict | None:
    """Похожая модель ещё не экземпляр: нужны совпадающие год и пробег.

    При неполных данных отвечаем по объявлению, без чужих VIN и отчёта.
    Расхождение пробега требует проверки человеком.
    """
    listing = parse_listing(title, price_string)
    found = fitting_cards(title, price_string, stock=stock)
    if len(found) == 1:
        card = found[0]
        if (listing["year"] and listing["year"] == card.get("year")
                and listing["km"] and card.get("km")
                and listing["km"] == card["km"]):
            return card
    return None


def attached_listing(doc: dict | None) -> dict | None:
    """Объявление, привязанное к чату. Реплика клиента это не заменяет."""
    doc = doc or {}
    av = doc.get("avito") or {}
    ar = doc.get("autoru") or {}
    src = av if (av.get("title") or av.get("url")) else ar
    title = str((src or {}).get("title") or "").strip()
    low = title.lower()
    if not title or "не привязано" in low or low in {"объявление", "чат"}:
        return None
    return src


def report_link(doc: dict) -> str:
    """Только ссылка из поля Автотека подтверждённого экземпляра."""
    src = attached_listing(doc)
    if not src:
        return ""
    card = match_card(src.get("title") or "", src.get("price") or "")
    if not card:
        return ""
    match = re.search(r"https?://[^\s<>]+", card.get("Автотека") or "")
    return match.group(0) if match else ""


_PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+|\d{6,9})(?!\d)")


def price_amounts(text: str) -> list[int]:
    """Суммы, похожие на цену машины. Пробег, год и телефон не считаются."""
    masked = re.sub(r"https?://\S+", " ", text or "", flags=re.I)
    masked = re.sub(r"\d[\d\s\u00a0]{0,14}\s*км\b", " ", masked, flags=re.I)
    masked = re.sub(r"\b(?:19|20)\d{2}\b", " ", masked)
    found: list[int] = []
    for match in _PRICE_RE.finditer(masked):
        number = int(re.sub(r"\D", "", match.group(1)))
        if number < 100_000 or number > 80_000_000:
            continue
        if number % 500 != 0:
            continue
        found.append(number)
    return found


def allowed_prices(doc: dict | None) -> set[int]:
    """Цены, которые можно назвать в чате с объявлением.

    Это цена объявления, НДС от неё и, если приклеилась ровно одна карточка,
    цена этой карточки. Цены соседних машин сюда не входят.
    """
    src = attached_listing(doc)
    if not src:
        return set()
    allowed: set[int] = set()
    listing_n = digits(str(src.get("price") or ""))
    if listing_n:
        allowed.add(listing_n)
    card = match_card(src.get("title") or "", src.get("price") or "")
    if card and card.get("price"):
        allowed.add(int(card["price"]))
    vat_n = digits(vat_for(str(src.get("price") or ""), card))
    if vat_n:
        allowed.add(vat_n)
    return allowed


def drop_foreign_prices(text: str, allowed: set[int]) -> str:
    """Убирает фразу, в которой названа чужая цена."""
    if not allowed:
        return text
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept: list[str] = []
    changed = False
    for part in parts:
        if not part.strip():
            continue
        foreign = [n for n in price_amounts(part) if n not in allowed]
        if foreign:
            changed = True
            continue
        kept.append(part.strip())
    if not changed:
        return text
    return " ".join(kept).strip()


def vat_for(price_string: str = "", card: dict | None = None) -> str:
    """Готовая цена с НДС: строка карточки или наличные плюс 15% до 50 тысяч."""
    from tools.stock_sync import vat_price

    if card:
        raw = card.get("raw") or ""
        found = re.search(r"Цена на юрлицо с НДС:\s*([0-9\s]+руб\.)", raw)
        if found:
            return found.group(1).strip()
        cash = card.get("Цена в объявлении") or ""
        if cash and "не указана" not in cash.lower():
            got = vat_price(cash)
            if got:
                return got
    if price_string:
        return vat_price(price_string) or ""
    return ""


def focus_block(
    title: str,
    price_string: str = "",
    url: str = "",
    cme_id: str = "",
    channel: str = "Авито",
    stock: str = "",
) -> str:
    card = match_card(title, price_string, stock=stock)
    lines = [
        "# Клиент пишет по объявлению %s" % (channel or "Авито"),
        "Объявление: %s" % (title or "не названо"),
    ]
    if price_string:
        lines.append("Цена в объявлении: %s" % price_string)
    if url:
        lines.append("Ссылка: %s" % url)
    if cme_id:
        lines.append("Код выгрузки CM Expert: %s" % cme_id)
    if card:
        vin = (card.get("VIN") or "").split()[0] if card.get("VIN") else ""
        lines.append("Это машина из стока: %s%s." % (card.get("title"), (" VIN " + vin) if vin else ""))
        lines.append("Отвечай по её карточке, не спрашивай «какой автомобиль смотрите».")
        lines.append(
            "Модель объявления и карточки одна. V-класс это не G-класс. "
            "Клиент сказал, что в отчёте другая модель, - признай ошибку, "
            "чужой VIN и чужую автотеку не защищай."
        )
        lines.append(
            "Клиент уже на этом объявлении. Не представляй её заново списком "
            "«этот экземпляр и ещё вот тот». Бензин, дизель, год, комплектация "
            "и «гелик смотрю» — про неё. Другие машины называй, только если "
            "спросил что ещё есть или эта не подходит."
        )
        if price_string and not card.get("price"):
            lines.append(
                "Цена для клиента — из объявления выше. В карточке стока цены "
                "может не быть. Клиенту про пустую базу и «цены нет» ни слова."
            )
        vat = vat_for(price_string, card)
        if vat:
            lines.append(
                "Цена на юрлицо с НДС: %s. Клиент спросил НДС, юрлицо или счёт — "
                "называй эту цифру в этот чат. Не считай сам, не пиши «уточню», "
                "«нет под рукой», «бухгалтерия». Номер из-за этой цифры не проси."
                % vat
            )
        lines.append(
            "Просят автотеку или отчёт, и в карточке есть ссылка — кинь ссылку "
            "и остановись. ДТП, даты, ремонт и кузов в эту реплику не пиши. "
            "Спросили именно про ДТП или кузов — один спокойный факт, без "
            "перечня аварий. Номер и «наберу» из-за отчёта не проси. "
            "Если ссылки нет, а машина с пробегом — не говори «автотеки нет». "
            "Лизинг нашей машины: спросили «была?» — «нет, выкуплена». "
            "Отчёт и «на сделке покажем» сам не тащи."
        )
        lines.append(
            "Оплата салона: наличные или расчётный счёт с НДС. Кредит как продукт "
            "не оформляем. Спросили «в кредит?» — цена за наличный расчёт и номер. "
            "Банки, «кредит рассматриваем» и кредитного специалиста в чат не пиши."
        )
        lines.append(card.get("raw") or "")
    else:
        lines.append(
            "Карточка стока к объявлению не подцепилась. Клиенту про это ни слова: "
            "не пиши про сток, VIN, сверку, «не поднимал автотеку», «карточки нет», "
            "«под рукой нет», «наугад», «чтобы не дезинформировать». "
            "Известны только данные объявления. Цена только из строки «Цена в объявлении» "
            "выше. «В базе проходит», чужой VIN и цену другой машины не пиши. "
            "На ДТП и историю цифры не выдумывай. "
            "Не подтверждай наличие по одному объявлению. "
            "Запрос отчёта без подтверждённой карточки передай человеку: [[ЧЕЛОВЕК]]. "
            "Телефон чтобы позвонить не проси, пока клиент сам его не дал. "
            "Кредит не оформляем: спросили — наличный расчёт и номер, банки не называй."
        )
        vat = vat_for(price_string)
        if vat:
            lines.append(
                "Цена на юрлицо с НДС по цене объявления: %s. "
                "Клиент спросил НДС — называй эту цифру в чат, номер не проси."
                % vat
            )
    return "\n".join(lines)


def focus_from_doc(doc: dict) -> str:
    """Свежий фокус по объявлению: сток мог обновиться после первого сообщения."""
    av = doc.get("avito") or {}
    ar = doc.get("autoru") or {}
    src = av if (av.get("title") or av.get("url") or av.get("cme_id")) else ar
    if not src:
        return ""
    if not (src.get("title") or src.get("url")):
        return src.get("focus") or ""
    channel = "Авито" if src is av else "Авто.ру"
    return focus_block(
        src.get("title") or "",
        src.get("price") or "",
        src.get("url") or "",
        src.get("cme_id") or "",
        channel=channel,
    )


def listing_price(doc: dict | None) -> str:
    """Цена объявления, как её видит клиент. Не цена чужой карточки."""
    doc = doc or {}
    av = doc.get("avito") or {}
    ar = doc.get("autoru") or {}
    src = av if (av.get("price") or av.get("title")) else ar
    return " ".join(str((src or {}).get("price") or "").split())


def vat_from_doc(doc: dict | None) -> str:
    doc = doc or {}
    av = doc.get("avito") or {}
    ar = doc.get("autoru") or {}
    src = av if (av.get("title") or av.get("price")) else ar
    if not src:
        return ""
    return vat_for(src.get("price") or "", match_card(src.get("title") or "", src.get("price") or ""))
