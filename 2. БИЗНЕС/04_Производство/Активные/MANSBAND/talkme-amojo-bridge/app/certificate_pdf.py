"""Certificate layout measured from the owner reference, with original site fonts/logo."""
from io import BytesIO
from pathlib import Path
from html import escape
import json
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white, black

CONTACTS={
 "telegram":"https://t.me/Mansband_manager",
 "whatsapp":"https://wa.me/79310090251",
 "max":"https://max.ru/u/f9LHodD0cOL0gYEHs3bHchlNHVG8jZyR6C4wxQuNkJuVd4ndAyq2r-8QNcY",
 "pyat":"https://yandex.ru/maps/org/mans_band/138838694193/",
 "baum":"https://yandex.ru/maps/org/mans_band/117054627870/",
 "site":"https://mansband.ru/"}
DEFAULT_INTRO="Поздравляем с приобретением подарочного сертификата MANSBAND!"
DEFAULT_INSTRUCTIONS="1. Прийти на примерку в любой магазин MANSBAND.\n2. Предоставить подарочный сертификат либо назвать его номер.\n3. Довериться стилисту с выбором образа."

# Reference is 538 × 1108 CSS pixels. PDF uses 0.75 pt per CSS pixel.
PAGE_WIDTH, PAGE_HEIGHT = 538, 1108
ASSETS = Path(__file__).parent / "assets"
SOCIALS = {
    "VK": "https://vk.com/mans__band",
    "Instagram": "https://instagram.com/mans__band",
    "YouTube": "https://www.youtube.com/@Mans_band",
    "Дзен": "https://dzen.ru/id/6422b52c1bcfed405046f8af",
}


def render_pdf(data, config=None):
    config = config or {}
    contacts = {**CONTACTS, **config.get("contacts", {})}
    from urllib.parse import urlparse
    for value in contacts.values():
        u = urlparse(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("Недопустимая ссылка в PDF")
    for weight in (400, 600, 700):
        name = f"Montserrat{weight}"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(ASSETS / f"Montserrat-{weight}.ttf")))
    logo = json.loads((ASSETS / "mansband-logo-paths.json").read_text())
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(PAGE_WIDTH * .75, PAGE_HEIGHT * .75))
    c.setTitle("Подарочный сертификат MANSBAND")
    c.setAuthor("MANSBAND")
    y = 84

    def page():
        c.scale(.75, .75)
        c.setFillColor(HexColor("#f5f5f5"))
        c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
        c.setFillColor(white)
        c.rect(8, PAGE_HEIGHT - 1014, 528, 1014, fill=1, stroke=0)
        c.setFillColor(black)
        c.rect(8, PAGE_HEIGHT - 58, 528, 58, fill=1, stroke=0)
        c.saveState()
        c.translate(40, PAGE_HEIGHT - 21)
        c.scale(.16, -.16)
        c.setFillColor(white)
        for commands in logo:
            path = c.beginPath()
            for method, points in commands:
                if method == "closePath": path.close()
                elif method == "endPath": pass
                else:
                    getattr(path, method)(*[coordinate for point in points for coordinate in point])
            c.drawPath(path, fill=1, stroke=0)
        c.restoreState()
        c.setFillColor(white)
        c.setFont("Montserrat700", 9)
        c.drawRightString(504, PAGE_HEIGHT - 32, "ПОДАРОЧНЫЙ СЕРТИФИКАТ")

    def new_page():
        nonlocal y
        c.showPage()
        page()
        y = 84

    def paragraph(value, size=12.8, leading=21, weight=400, color="#000000",
                  x=40, width=464, markup=False, advance=True):
        nonlocal y
        style = ParagraphStyle("certificate", fontName=f"Montserrat{weight}",
                               fontSize=size, leading=leading, textColor=HexColor(color))
        content = value if markup else escape(str(value)).replace("\n", "<br/>")
        p = Paragraph(content, style)
        _, height = p.wrap(width, 100000)
        if y + height > 990:
            pieces = p.split(width, 990 - y)
            if pieces:
                first = pieces.pop(0)
                _, h = first.wrap(width, 100000)
                first.drawOn(c, x, PAGE_HEIGHT - y - h)
                y += h
                # Split long greetings without truncating any text.
                for rest in pieces:
                    new_page()
                    while True:
                        _, h = rest.wrap(width, 100000)
                        if h <= 904:
                            rest.drawOn(c, x, PAGE_HEIGHT - y - h)
                            y += h
                            break
                        chunks = rest.split(width, 904)
                        head, rest = chunks
                        _, h = head.wrap(width, 100000)
                        head.drawOn(c, x, PAGE_HEIGHT - y - h)
                        new_page()
                return height
            new_page()
        p.drawOn(c, x, PAGE_HEIGHT - y - height)
        if advance: y += height
        return height

    def gap(amount):
        nonlocal y
        y += amount

    def divider():
        c.setStrokeColor(HexColor("#e7e7e7"))
        c.setLineWidth(.7)
        c.line(8, PAGE_HEIGHT - y, 536, PAGE_HEIGHT - y)

    def link(label, url, underline=True):
        label = escape(label)
        if underline: label = "<u>" + label + "</u>"
        return f'<link href="{escape(url, quote=True)}" color="#000000">{label}</link>'

    page()
    paragraph("Здравствуйте" + (f", {data['guest']}" if data.get("guest") else "") + "!",
              size=17.6, leading=22, weight=600)
    gap(20)
    paragraph(config.get("intro", DEFAULT_INTRO))
    gap(21)
    divider()  # y=189 for the reference's two-line introduction.
    gap(29)
    paragraph("Номер сертификата", color="#c5c5c5", advance=False)
    paragraph("Действителен до", color="#c5c5c5", x=280, width=224)
    paragraph(data["number"], width=224, advance=False)
    paragraph(data["until"], x=280, width=224)
    gap(8)
    paragraph("Номинал сертификата", color="#c5c5c5")
    paragraph(f"{data['nominal']:,.0f} ₽".replace(",", " "), size=18, leading=24, weight=600)
    gap(24)
    divider()
    greeting = str(data.get("greeting") or "").strip()
    if greeting:
        gap(27)
        paragraph("Поздравление отправителя:", size=14.35, leading=21, weight=600)
        gap(13)
        paragraph(greeting)
        gap(24)
        divider()
    gap(26)
    paragraph("Как воспользоваться подарочным сертификатом?", size=14.35, leading=21, weight=600)
    gap(13)
    instructions = config.get("instructions", DEFAULT_INSTRUCTIONS).splitlines()
    for i, item in enumerate(instructions):
        import re
        item = re.sub(r"^(\d+\.)\s*", r"\1&nbsp; ", escape(item))
        paragraph(item, markup=True)
        if i < len(instructions) - 1: gap(8)
    gap(24)
    divider()
    gap(26)
    paragraph("Адреса магазинов MANSBAND:", size=14.35, leading=21, weight=600)
    gap(13)
    paragraph("Магазин Новокузнецкая: " + link("г. Москва, ул. Пятницкая, д.8", contacts["pyat"]), markup=True)
    gap(8)
    paragraph("Магазин Бауманская: " + link("г. Москва, Спартаковская пл., д.14, стр 2", contacts["baum"]), markup=True)
    gap(24)
    paragraph("Пн-Пт: 12:00-22:00\nСб-Вс: 11:00-22:00\nПримерка без записи")
    gap(8)
    paragraph("Бесплатная парковка (звоните за 2 мин)")
    gap(8)
    paragraph("Телефон: " + link("+7 931 009-02-51", "tel:+79310090251"), markup=True)
    gap(23)
    if y + 78 > 1014: new_page()
    c.setFillColor(black)
    c.roundRect(40, PAGE_HEIGHT-y-40, 464, 40, 4, stroke=0, fill=1)
    c.setFont("Montserrat400", 13)
    c.setFillColor(white)
    c.drawCentredString(272, PAGE_HEIGHT-y-25, "Перейти на сайт")
    c.linkURL(contacts["site"], (40, PAGE_HEIGHT-y-40, 504, PAGE_HEIGHT-y), relative=1)
    gap(57)
    # Keep the reference's social links on one line; add the approved delivery channels.
    links = [("VK", SOCIALS["VK"]), ("Instagram", SOCIALS["Instagram"]),
             ("Telegram", contacts["telegram"]), ("YouTube", SOCIALS["YouTube"]),
             ("Дзен", SOCIALS["Дзен"]), ("WhatsApp", contacts["whatsapp"]), ("MAX", contacts["max"])]
    paragraph("  ".join(link(label, url, False) for label, url in links), markup=True)
    y = max(1038, y + 57)
    # A messenger delivery must not claim that the recipient supplied an email address.
    footer = ("Вы получили подарочный сертификат магазина MANSBAND. Если вы считаете, "
              "что это сообщение попало к вам по ошибке, сообщите нам по указанному телефону.")
    style = ParagraphStyle("footer", fontName="Montserrat400", fontSize=11, leading=19, textColor=HexColor("#c5c5c5"))
    p = Paragraph(footer, style)
    _, h = p.wrap(464, 100)
    p.drawOn(c, 40, PAGE_HEIGHT-y-h)
    c.save()
    result = out.getvalue()
    if len(result) > 9 * 1024 * 1024:
        raise ValueError("PDF превышает допустимый размер")
    return result
