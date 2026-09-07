"""Этикетки отправлений одним PDF.

Сергей в ЛК выделяет заказы одного артикула и жмёт «Этикетки» — получается лента,
которую он клеит по одной наклейке на товар. Здесь то же самое, но сразу по обеим
площадкам и с выбором, печатать ли рядом штрихкод самого товара.

Что отдают площадки:

- **Ozon** `/v2/posting/fbs/package-label` — готовый PDF на все отправления, до 20 за
  запрос, и только для статуса `awaiting_deliver`. Формат ответа в документации
  описан противоречиво (то `application/pdf`, то JSON с `file_content`), поэтому
  тело разбираем по сигнатуре. Размер страницы зависит от настройки кабинета
  «Печатать этикетку 58 × 40», параметра в API нет — поэтому страницы вписываем
  в наш формат сами.
- **Wildberries** `/api/v3/orders/stickers` — по стикеру на заказ, base64 PNG,
  до 100 заказов, статусы `confirm` и `complete`.

Штрихкод товара площадки в этикетку не добавляют: у Ozon такого параметра нет
вообще. Рисуем его сами из штрихкода позиции.
"""

import base64
import io
import re
import time

from net import OZON_BASE, WB_BASE, ozon_headers, req, wb_headers

# Лента 58×40 мм — то, на чём печатает склад.
LABEL_W_MM = 58.0
LABEL_H_MM = 40.0
MM = 72.0 / 25.4  # мм в типографские пункты

OZON_CHUNK = 20   # жёсткий предел метода
WB_CHUNK = 100    # жёсткий предел метода

# Режимы печати. Сергей просил выбор: у него штрихкод товара бывает заранее
# проклеен, а бывает нужен рядом с этикеткой отправления.
MODE_POSTING = "posting"
MODE_BOTH = "both"
MODE_PRODUCT = "product"
MODES = (MODE_POSTING, MODE_BOTH, MODE_PRODUCT)


class LabelError(Exception):
    pass


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --- Ozon ---------------------------------------------------------------


def _ozon_pdf_bytes(resp):
    """Достаём PDF из ответа: он бывает сырыми байтами, а бывает JSON-полем."""
    body = resp.content or b""
    if body[:4] == b"%PDF":
        return body
    text = body.lstrip()[:1]
    if text in (b"{", b"["):
        try:
            data = resp.json()
        except ValueError:
            raise LabelError("Ozon вернул нераспознанный ответ на запрос этикеток")
        raw = data.get("file_content") or (data.get("result") or {}).get("file_content") or data.get("content")
        if not raw:
            msg = data.get("message") or data.get("code") or ""
            raise LabelError("Ozon не отдал этикетки%s" % (": %s" % msg if msg else ""))
        if isinstance(raw, str):
            # в JSON PDF приходит не base64, а байтами в latin-1; base64 пробуем вторым
            guess = raw.encode("latin-1", "ignore")
            if guess[:4] == b"%PDF":
                return guess
            try:
                dec = base64.b64decode(raw, validate=False)
                if dec[:4] == b"%PDF":
                    return dec
            except Exception:
                pass
            raise LabelError("Ozon отдал этикетки в неизвестной кодировке")
        return bytes(raw)
    raise LabelError("Ozon вернул не PDF (%s байт)" % len(body))


def ozon_labels(cab, postings):
    """PDF с этикетками отправлений Ozon. Возвращает (список pdf, список заметок)."""
    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    pdfs = []
    notes = []
    for chunk in _chunks(list(postings), OZON_CHUNK):
        r = req(
            "POST",
            OZON_BASE + "/v2/posting/fbs/package-label",
            headers=heads,
            json={"posting_number": list(chunk)},
        )
        if r.status_code == 200:
            pdfs.append(_ozon_pdf_bytes(r))
            continue
        detail = (r.text or "")[:300]
        # «этикетки ещё не готовы» — Ozon просит подождать 45-60 секунд после сборки
        if "aren't ready" in detail or "not ready" in detail.lower():
            notes.append("Ozon: этикетки ещё не готовы, площадка формирует их до минуты после сборки. Попробуй ещё раз.")
            continue
        if r.status_code == 429:
            notes.append("Ozon: слишком часто запрашиваем этикетки, подожди немного.")
            continue
        notes.append("Ozon: не отдал этикетки (%s) %s" % (r.status_code, detail))
    return pdfs, notes


# --- Wildberries --------------------------------------------------------


def wb_stickers(cab, order_ids):
    """Стикеры WB: {id заказа: png}. Плюс заметки о том, что не получилось."""
    out = {}
    notes = []
    ids = []
    for raw in order_ids:
        digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
        if digits:
            ids.append(int(digits))
    for chunk in _chunks(ids, WB_CHUNK):
        r = req(
            "POST",
            WB_BASE + "/api/v3/orders/stickers",
            headers=wb_headers(cab["token"]),
            params={"type": "png", "width": 58, "height": 40},
            json={"orders": list(chunk)},
        )
        if r.status_code == 402:
            notes.append("WB: доступ к стикерам требует оплаты тарифа у клиента.")
            continue
        if r.status_code != 200:
            notes.append("WB: не отдал стикеры (%s) %s" % (r.status_code, (r.text or "")[:200]))
            continue
        try:
            data = r.json()
        except ValueError:
            notes.append("WB: нераспознанный ответ на запрос стикеров")
            continue
        for item in data.get("stickers") or []:
            if not isinstance(item, dict):
                continue
            raw = item.get("file") or ""
            try:
                png = base64.b64decode(raw)
            except Exception:
                continue
            if png:
                out[str(item.get("orderId") or "")] = {
                    "png": png,
                    "part_a": str(item.get("partA") or ""),
                    "part_b": str(item.get("partB") or ""),
                }
    return out, notes


# --- сборка PDF ---------------------------------------------------------


def _canvas():
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(LABEL_W_MM * MM, LABEL_H_MM * MM))
    return buf, c


FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # образ, fonts-dejavu-core
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS, разработка
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_font_name = None


def _font():
    """Шрифт с кириллицей. Встроенные в reportlab Vera и Helvetica её не умеют:
    без внешнего шрифта артикул и наименование печатались бы квадратами."""
    global _font_name
    if _font_name:
        return _font_name
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in FONT_PATHS:
        try:
            face = TTFont("FFLabel", path)
            # проверяем именно кириллицу, а не просто что файл открылся
            if not all(face.face.charToGlyph.get(ord(ch)) for ch in "Артикул"):
                continue
            pdfmetrics.registerFont(face)
            _font_name = "FFLabel"
            return _font_name
        except Exception:
            continue
    print("этикетки: не нашёл шрифт с кириллицей, печатаю базовым — русский текст будет искажён")
    _font_name = "Helvetica"
    return _font_name


def _shorten(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ean13_valid(code):
    """Проверка контрольной цифры EAN-13.

    Обязательна: reportlab молча отбрасывает 13-ю цифру и считает контрольную
    сам. Если в штрихкоде клиента опечатка, он напечатал бы штрихкод, которого
    нет в нашей базе, — и склад наклеил бы его на товар. Кодируем как EAN-13
    только то, что сходится; всё остальное уходит в Code128 дословно.
    """
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(code[:12]))
    return (10 - total % 10) % 10 == int(code[12])


def product_label(c, row, font):
    """Этикетка товара: штрихкод позиции, артикул, наименование.

    Нужна тем, у кого штрихкод не проклеен заранее. Рисуем сами — у Ozon
    параметра «добавить штрихкод товара» в API нет.
    """
    from reportlab.graphics.barcode import code128, eanbc

    w = LABEL_W_MM * MM
    h = LABEL_H_MM * MM
    code = "".join(ch for ch in str(row.get("barcode") or "") if ch.isdigit())
    drawn = False
    if ean13_valid(code):
        try:
            bc = eanbc.Ean13BarcodeWidget(code, barHeight=13 * MM, humanReadable=True)
            d = bc.asDrawing(w * 0.86, 15 * MM)
            d.drawOn(c, w * 0.07, h * 0.30)
            drawn = True
        except Exception:
            drawn = False
    hint = ""
    if not drawn:
        # Code128 кодирует строку дословно: то, что отсканируют, равно тому, что
        # лежит у нас в базе. Сюда попадают внутренние коды Ozon, штрихкоды с
        # несходящейся контрольной цифрой и позиции вообще без штрихкода.
        raw = str(row.get("barcode") or "").strip()
        if not raw:
            raw = str(row.get("article") or "").strip()
            if raw:
                hint = "артикул, штрихкода нет"
        if raw:
            bc = code128.Code128(raw, barHeight=12 * MM, barWidth=0.33 * MM, humanReadable=True)
            bc.drawOn(c, max(2 * MM, (w - bc.width) / 2), h * 0.30)
            drawn = True
    if not drawn:
        c.setFont(font, 8)
        c.drawCentredString(w / 2, h * 0.45, "нет штрихкода и артикула")
    if hint:
        # ниже цифр под штрихкодом (те идут примерно с 9.5 мм) и выше имени клиента
        c.setFont(font, 5)
        c.drawCentredString(w / 2, 5.6 * MM, hint)
    c.setFont(font, 8)
    c.drawString(3 * MM, h - 6 * MM, _shorten(row.get("article"), 26))
    c.setFont(font, 6)
    c.drawString(3 * MM, h - 9.5 * MM, _shorten(row.get("name"), 46))
    c.setFont(font, 5.5)
    c.drawString(3 * MM, 2.5 * MM, _shorten(row.get("client"), 40))
    c.showPage()


def png_label(c, png, caption, font):
    """Стикер площадки картинкой на всю этикетку."""
    from reportlab.lib.utils import ImageReader

    w = LABEL_W_MM * MM
    h = LABEL_H_MM * MM
    img = ImageReader(io.BytesIO(png))
    iw, ih = img.getSize()
    scale = min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    c.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh)
    if caption:
        c.setFont(font, 5)
        c.drawString(1.5 * MM, 1.2 * MM, _shorten(caption, 46))
    c.showPage()


def _fit_pages(writer, pdfs):
    """Страницы PDF площадки вписываем в нашу ленту 58×40.

    Размер страницы у Ozon зависит от настройки кабинета, в API его не выбрать.
    Поэтому не доверяем формату: масштабируем с сохранением пропорций.
    """
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import RectangleObject

    target_w = LABEL_W_MM * MM
    target_h = LABEL_H_MM * MM
    added = 0
    sizes = set()
    for blob in pdfs:
        try:
            reader = PdfReader(io.BytesIO(blob))
        except Exception:
            continue
        for page in reader.pages:
            box = page.mediabox
            pw = float(box.width) or target_w
            ph = float(box.height) or target_h
            sizes.add((round(pw / MM), round(ph / MM)))
            scale = min(target_w / pw, target_h / ph)
            blank = PdfWriter().add_blank_page(width=target_w, height=target_h)
            page.add_transformation(
                Transformation().scale(scale, scale).translate(
                    (target_w - pw * scale) / 2, (target_h - ph * scale) / 2
                )
            )
            page.mediabox = RectangleObject((0, 0, target_w, target_h))
            blank.merge_page(page)
            writer.add_page(blank)
            added += 1
    return added, sizes


def build_pngs(items):
    """PDF из готовых картинок площадки: QR грузомест и QR поставки.

    items — [{png, caption}]. Каждая картинка занимает отдельную этикетку 58×40:
    склад клеит их на коробки той же лентой, что и этикетки отправлений.
    """
    items = [x for x in items if x.get("png")]
    if not items:
        raise LabelError("Нечего печатать: площадка не отдала ни одного QR.")
    font = _font()
    buf, c = _canvas()
    for item in items:
        png_label(c, item["png"], item.get("caption") or "", font)
    c.save()
    return buf.getvalue(), len(items)


def build(rows, mode=MODE_POSTING, fetch=True):
    """Собирает один PDF на выбранные отправления.

    rows — записи из раздела «Сборка» с полями marketplace, ext_id, cabinet,
    article, barcode, name, client. Возвращает (pdf, заметки).
    """
    if mode not in MODES:
        raise LabelError("неизвестный режим печати %s" % mode)
    notes = []
    font = _font()

    platform_pdfs = []
    wb_png = {}
    if mode in (MODE_POSTING, MODE_BOTH) and fetch:
        by_cab = {}
        for row in rows:
            by_cab.setdefault(row["cabinet_id"], []).append(row)
        for cab_id, group in by_cab.items():
            cab = group[0]["cabinet"]
            if not cab:
                notes.append("Нет доступа к кабинету %s, этикетки не запрошены." % cab_id)
                continue
            if cab["marketplace"] == "ozon":
                pdfs, more = ozon_labels(cab, [r["ext_id"] for r in group])
                platform_pdfs.extend(pdfs)
                notes.extend(more)
            elif cab["marketplace"] == "wb":
                got, more = wb_stickers(cab, [r["ext_id"] for r in group])
                wb_png.update(got)
                notes.extend(more)

    buf, c = _canvas()
    own = 0
    for row in rows:
        if mode in (MODE_POSTING, MODE_BOTH) and row["marketplace"] == "wb":
            key = "".join(ch for ch in str(row["ext_id"]) if ch.isdigit())
            got = wb_png.get(key)
            if got:
                sign = " ".join(x for x in (got["part_a"], got["part_b"]) if x)
                png_label(c, got["png"], "%s · %s" % (row["ext_id"], sign), font)
                own += 1
        if mode in (MODE_BOTH, MODE_PRODUCT):
            product_label(c, row, font)
            own += 1
    c.save()

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    fitted, sizes = _fit_pages(writer, platform_pdfs)
    if own:
        for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
            writer.add_page(page)
    if not writer.pages:
        raise LabelError(
            "Нечего печатать. "
            + (
                "; ".join(notes)
                if notes
                else "Площадки не отдали этикетки: проверь, что отправления в статусе «Ожидают отгрузки» у Ozon и «На сборке» у WB."
            )
        )
    if sizes and sizes - {(round(LABEL_W_MM), round(LABEL_H_MM))}:
        notes.append(
            "Ozon отдал этикетки в формате %s мм — вписал в ленту 58×40. Чтобы печатать один в один, включи в кабинете Ozon «Печатать этикетку 58 × 40»."
            % ", ".join("%s×%s" % s for s in sorted(sizes))
        )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), notes, fitted + own
