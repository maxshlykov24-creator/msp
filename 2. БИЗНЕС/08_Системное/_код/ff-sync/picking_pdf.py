"""Лист подбора на печать A4: как у WB, вместо их логотипа — БЕРЕЗА ГРУПП."""

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

from db import catalog_card
from labels import _font, _font_bold, _wrap

MSK = timezone(timedelta(hours=3))
MARGIN = 12 * mm
ROW_H = 16 * mm
HEAD_H = 8 * mm
PHOTO = 13 * mm
# колонки как у WB: задание, фото, бренд, имя, размер, цвет, артикул, стикер
COLS = (
    ("№ задания", 26 * mm),
    ("Фото", 16 * mm),
    ("Бренд", 24 * mm),
    ("Наименование", 48 * mm),
    ("Размер", 14 * mm),
    ("Цвет", 18 * mm),
    ("Артикул", 18 * mm),
    ("Стикер", 22 * mm),
)


def _photo_url(url):
    """Превью каталога мелкое, на бумаге нужно крупнее."""
    raw = str(url or "")
    return (
        raw.replace("/images/tm/", "/images/c246x328/")
        .replace("/wc50/", "/wc200/")
        .replace("/wc100/", "/wc200/")
    )


def _fetch_photo(url):
    if not url:
        return None
    try:
        r = requests.get(_photo_url(url), timeout=8, headers={"User-Agent": "ff-sync-picking"})
        if r.status_code != 200 or not r.content:
            return None
        return ImageReader(io.BytesIO(r.content))
    except Exception:
        return None


def _title(rows, who):
    supplies = sorted({(r["supply_ext"] if "supply_ext" in r.keys() else "") or "" for r in rows} - {""})
    if len(supplies) == 1:
        return supplies[0]
    return who or "смена"


def _size(val):
    text = str(val or "").strip()
    return "" if text == "0" else text


def _rows(raw):
    out = []
    cards = {}
    for row in raw:
        article = (row["article"] or "").strip()
        barcode = (row["barcode"] or "").strip()
        key = (row["client_id"], article, barcode)
        if key not in cards:
            cards[key] = catalog_card(row["client_id"], barcode=barcode, article=article)
        card = cards[key]
        qty = float(row["qty"] or 0) or 1
        name = (row["name"] or "").strip() or card.get("name") or ""
        if qty != 1:
            name = ("%s · %s шт" % (name, int(qty) if qty.is_integer() else qty)).strip(" ·")
        out.append(
            {
                "ext_id": str(row["ext_id"] or ""),
                "image": (row["image"] or "").strip(),
                "brand": (card.get("brand") or "").strip(),
                "name": name,
                "size": _size(card.get("size")),
                "color": (card.get("color") or "").strip(),
                "article": article,
                "sticker": (row["track"] or "").strip() or barcode,
                "qty": qty,
            }
        )
    return out


def _header(c, page_w, page_h, title, qty, pages, page):
    font, bold = _font(), _font_bold()
    top = page_h - MARGIN
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.setFont(font, 9)
    c.drawString(MARGIN, top, "Дата: %s" % datetime.now(MSK).strftime("%d.%m.%Y"))
    c.setFont(bold, 13)
    c.drawString(MARGIN, top - 16, "Лист подбора  %s" % title)
    c.setFont(font, 9)
    c.drawString(MARGIN, top - 30, "Количество товаров: %s" % qty)
    c.setFillColorRGB(0.12, 0.28, 0.16)
    c.setFont(bold, 13)
    c.drawRightString(page_w - MARGIN, top - 4, "БЕРЕЗА ГРУПП")
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.setFont(font, 7)
    c.drawRightString(page_w - MARGIN, top - 16, "стр. %s из %s" % (page, pages))
    c.setFillColorRGB(0, 0, 0)
    return top - 42


def _table_head(c, y, page_w):
    font = _font()
    x = MARGIN
    c.setFillColorRGB(0.94, 0.94, 0.94)
    c.rect(MARGIN, y - HEAD_H, page_w - 2 * MARGIN, HEAD_H, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.4)
    c.rect(MARGIN, y - HEAD_H, page_w - 2 * MARGIN, HEAD_H, fill=0, stroke=1)
    c.setFont(font, 7)
    for title, width in COLS:
        c.drawString(x + 2, y - HEAD_H + 3, title)
        x += width
        c.line(x, y - HEAD_H, x, y)
    return y - HEAD_H


def _cell(c, text, x, y, w, h, font, size, lines=2):
    bits = _wrap(text, font, size, w - 4, lines)
    if not bits:
        return
    step = 9
    top = y + h / 2 + (len(bits) - 1) * step / 2 - 3
    c.setFont(font, size)
    for i, line in enumerate(bits):
        c.drawString(x + 2, top - i * step, line)


def _photo(c, img, x, y, w, h):
    box = min(PHOTO, w - 3, h - 3)
    if img is None:
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.rect(x + (w - box) / 2, y + (h - box) / 2, box, box, fill=0, stroke=1)
        return
    try:
        c.drawImage(img, x + (w - box) / 2, y + (h - box) / 2, box, box, preserveAspectRatio=True, mask="auto")
    except Exception:
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.rect(x + (w - box) / 2, y + (h - box) / 2, box, box, fill=0, stroke=1)


def _draw_row(c, item, photo, y, page_w):
    font = _font()
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.4)
    c.rect(MARGIN, y - ROW_H, page_w - 2 * MARGIN, ROW_H, fill=0, stroke=1)
    x = MARGIN
    values = (
        item["ext_id"],
        None,
        item["brand"],
        item["name"],
        item["size"],
        item["color"],
        item["article"],
        item["sticker"],
    )
    for i, ((_, width), val) in enumerate(zip(COLS, values)):
        if i == 1:
            _photo(c, photo, x, y - ROW_H, width, ROW_H)
        else:
            _cell(c, val, x, y - ROW_H, width, ROW_H, font, 7, lines=3 if i == 3 else 2)
        x += width
        c.line(x, y - ROW_H, x, y)


def build_picking_pdf(rows, who=""):
    """PDF A4, одна строка — одно задание, фото из каталога."""
    items = _rows(rows)
    title = _title(rows, who)
    qty = int(round(sum(i["qty"] for i in items))) if items else 0
    urls = list({i["image"] for i in items if i["image"]})
    photos = {}
    if urls:
        with ThreadPoolExecutor(max_workers=8) as pool:
            photos = dict(zip(urls, pool.map(_fetch_photo, urls)))

    page_w, page_h = A4
    usable = page_h - MARGIN - 48 - HEAD_H
    per_page = max(1, int(usable // ROW_H))
    pages = max(1, (len(items) + per_page - 1) // per_page) if items else 1

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Лист подбора %s" % title)
    offset = 0
    for page in range(1, pages + 1):
        y = _header(c, page_w, page_h, title, qty, pages, page)
        y = _table_head(c, y, page_w)
        chunk = items[offset : offset + per_page]
        if not chunk and page == 1:
            c.setFont(_font(), 9)
            c.drawString(MARGIN + 4, y - 20, "В выборке нет отправлений: проверь контрагента, смену и вкладку")
        for item in chunk:
            _draw_row(c, item, photos.get(item["image"]), y, page_w)
            y -= ROW_H
        c.showPage()
        offset += per_page
    c.save()
    name = "лист_подбора_%s_%s.pdf" % (
        "".join(ch if ch.isalnum() else "_" for ch in title)[:40] or "smena",
        datetime.now(MSK).strftime("%Y%m%d_%H%M"),
    )
    return name, buf.getvalue(), pages
