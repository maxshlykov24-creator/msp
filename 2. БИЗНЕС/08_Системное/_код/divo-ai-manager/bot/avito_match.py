"""Связка объявления Авито с карточкой стока."""
from __future__ import annotations

import re

from bot.config import settings

YEAR_RE = re.compile(r"\b(20\d{2})\b")
KM_RE = re.compile(r"([\d\s\u00a0]+)\s*км", re.I)


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
        fields = {"title": title, "raw": "## " + chunk.strip()}
        for line in lines[1:]:
            if line.startswith("- ") and ":" in line:
                key, val = line[2:].split(":", 1)
                fields[key.strip()] = val.strip()
        fields["price"] = digits(fields.get("Цена в объявлении", ""))
        fields["year"] = digits(YEAR_RE.search(title).group(1) if YEAR_RE.search(title) else "")
        fields["km"] = digits(fields.get("Пробег", ""))
        out.append(fields)
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", (text or "").lower().replace("ё", "е")).strip()


def score(listing: dict, card: dict) -> int:
    points = 0
    head = _norm(listing.get("head") or listing.get("title") or "")
    title = _norm(card.get("title") or "")
    if not head or not title:
        return 0
    if head and head in title:
        points += 8
    tokens = [t for t in head.split() if len(t) > 2]
    hit = sum(1 for t in tokens if t in title)
    points += hit * 2
    if listing.get("year") and listing["year"] == card.get("year"):
        points += 4
    lp, cp = listing.get("price") or 0, card.get("price") or 0
    if lp and cp and abs(lp - cp) <= 1000:
        points += 6
    lk, ck = listing.get("km") or 0, card.get("km") or 0
    if lk and ck and abs(lk - ck) <= 50:
        points += 3
    return points


def match_card(title: str, price_string: str = "", stock: str = "") -> dict | None:
    if not stock:
        path = settings.kb / "сток" / "СТОК.md"
        stock = path.read_text(encoding="utf-8") if path.exists() else ""
    listing = parse_listing(title, price_string)
    ranked = sorted((_cards(stock)), key=lambda c: score(listing, c), reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    if score(listing, best) < 8:
        return None
    return best


def focus_block(
    title: str,
    price_string: str = "",
    url: str = "",
    cme_id: str = "",
    channel: str = "Авито",
) -> str:
    card = match_card(title, price_string)
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
        lines.append(card.get("raw") or "")
    else:
        lines.append(
            "Точного VIN в стоке не нашёл. Не выдумывай машину. "
            "Работай по тому, что написано в объявлении, и при сомнении уточни модель."
        )
    return "\n".join(lines)
