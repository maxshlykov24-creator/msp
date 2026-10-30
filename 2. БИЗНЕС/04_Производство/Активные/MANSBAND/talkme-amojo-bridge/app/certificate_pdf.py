"""Server-generated certificate. Text is escaped, links are fixed HTTPS contacts."""
from io import BytesIO
from pathlib import Path
from html import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white,black
from reportlab.lib.units import mm

CONTACTS={
 "telegram":"https://t.me/Mansband_manager",
 "whatsapp":"https://wa.me/79310090251",
 "max":"https://max.ru/u/f9LHodD0cOL0gYEHs3bHchlNHVG8jZyR6C4wxQuNkJuVd4ndAyq2r-8QNcY",
 "pyat":"https://yandex.ru/maps/org/mans_band/138838694193/",
 "baum":"https://yandex.ru/maps/org/mans_band/117054627870/",
 "site":"https://mansband.ru/"}
DEFAULT_INTRO="Поздравляем с приобретением подарочного сертификата MANSBAND!"
DEFAULT_INSTRUCTIONS="1. Прийти на примерку в любой магазин MANSBAND.\n2. Предоставить подарочный сертификат либо назвать его номер.\n3. Довериться стилисту с выбором образа."

def render_pdf(data,config=None):
 config=config or {};contacts={**CONTACTS,**config.get("contacts",{})}
 from urllib.parse import urlparse
 for value in contacts.values():
  u=urlparse(value)
  if u.scheme!="https" or not u.hostname or u.username or u.password:raise ValueError("Недопустимая ссылка в PDF")
 if "Mansband" not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont("Mansband",str(Path(__file__).parent/"assets/DejaVuSans.ttf")))
 base=ParagraphStyle("base",fontName="Mansband",fontSize=9,leading=14,textColor=black,spaceAfter=7)
 heading=ParagraphStyle("heading",parent=base,fontSize=11,leading=16,spaceBefore=12,spaceAfter=9)
 muted=ParagraphStyle("muted",parent=base,fontSize=8,textColor=HexColor("#777777"))
 def text(value,style=base):return Paragraph(escape(str(value)).replace("\n","<br/>"),style)
 def link(label,url):return Paragraph(f'<link href="{escape(url,quote=True)}" color="#111111"><u>{escape(label)}</u></link>',base)
 out=BytesIO();doc=SimpleDocTemplate(out,pagesize=(148*mm,310*mm),rightMargin=11*mm,leftMargin=11*mm,topMargin=10*mm,bottomMargin=11*mm,title="Подарочный сертификат MANSBAND",author="MANSBAND")
 width=126*mm
 banner=Table([[Paragraph('<font color="white">MANSBAND</font>',ParagraphStyle("brand",parent=base,fontSize=20,leading=27)),Paragraph('<font color="white">ПОДАРОЧНЫЙ<br/>СЕРТИФИКАТ</font>',ParagraphStyle("badge",parent=base,fontSize=7,leading=10,alignment=2))]],colWidths=[width*0.56,width*0.44])
 banner.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),black),("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10)]))
 story=[banner,Spacer(1,13),text("Здравствуйте"+(f", {data['guest']}" if data.get("guest") else "")+"!",heading),text(config.get("intro",DEFAULT_INTRO)),Spacer(1,9)]
 info=Table([[text("Номер сертификата",muted),text("Действителен до",muted)],[text(data["number"]),text(data["until"])],[text("Номинал сертификата",muted),text("")],[text(f"{data['nominal']:,.0f} ₽".replace(","," "),heading),text("")]],colWidths=[width/2,width/2]);info.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0)]));story.append(info)
 story.extend([text("Действителен с "+data["start"],muted)])
 if data.get("greeting"):story.extend([text("Поздравление отправителя:",heading),text(data["greeting"])])
 story.extend([text("Как воспользоваться подарочным сертификатом?",heading),text(config.get("instructions",DEFAULT_INSTRUCTIONS)),text("Адреса магазинов MANSBAND:",heading),link("Новокузнецкая: Москва, ул. Пятницкая, д. 8",contacts["pyat"]),link("Бауманская: Москва, Спартаковская пл., д. 14, стр. 2",contacts["baum"]),text("Пн-Пт: 12:00-22:00\nСб-Вс: 11:00-22:00\nПримерка без записи\nБесплатная парковка (звоните за 2 минуты)"),link("Телефон: +7 931 009-02-51","tel:+79310090251"),link("Перейти на сайт",contacts["site"]),link("Telegram",contacts["telegram"]),link("WhatsApp",contacts["whatsapp"]),link("MAX",contacts["max"])])
 doc.build(story);result=out.getvalue()
 if len(result)>9*1024*1024:raise ValueError("PDF превышает допустимый размер")
 return result
