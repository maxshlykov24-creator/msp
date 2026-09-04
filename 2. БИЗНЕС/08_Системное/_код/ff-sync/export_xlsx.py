"""Выгрузка учёта в Excel по кнопке."""

import io
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from account import lot_rows, totals
from db import get_shipments_by_ids, list_shipment_marks, status_label
from ms import order_app_url

MSK = timezone(timedelta(hours=3))

COLUMNS = [
    ("Контрагент", "client", 26),
    ("Артикул", "article", 22),
    ("Штрихкод", "barcode", 16),
    ("GTIN", "gtin", 16),
    ("Наименование", "name", 40),
    ("Тип продукции", "tracking", 16),
    ("Габариты, см", "dims", 14),
    ("Литраж, л", "liters", 11),
    ("Тариф, ₽/л/сутки", "tariff", 14),
    ("Принято, шт", "qty_in", 12),
    ("Отгружено, шт", "shipped", 13),
    ("Остаток, шт", "qty", 12),
    ("Объём, л", "volume", 11),
    ("Принято", "received", 12),
    ("Дней на складе", "days", 15),
    ("Дней к оплате", "bill_days", 14),
    ("Литро-суток", "liter_days", 13),
    ("К выставлению, ₽", "total", 17),
]

QTY_COL = 12
VOLUME_COL = 13
MONEY_COL = 18

HEAD_FILL = PatternFill("solid", fgColor="005BFF")
SUM_FILL = PatternFill("solid", fgColor="EEF3FA")


def excel_text(raw):
    """xlsx не принимает управляющие символы КИЗ (GS 0x1D)."""
    text = str(raw if raw is not None else "")
    text = text.replace("\x1d", "").replace("\x1e", "").replace("\x1f", "")
    return "".join(ch for ch in text if ord(ch) >= 32 or ch in "\t\n\r")


def build(client_id=None, query="", marked=None):
    rows = lot_rows(client_id=client_id, query=query, marked=marked)
    sums = totals(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Учёт"
    ws.append([title for title, _key, _w in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        ws.append([row[key] for _title, key, _w in COLUMNS])
    ws.append([])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Итого: %s позиций" % sums["positions"]).font = Font(bold=True)
    ws.cell(row=last, column=QTY_COL, value=sums["qty"]).font = Font(bold=True)
    ws.cell(row=last, column=VOLUME_COL, value=sums["volume"]).font = Font(bold=True)
    ws.cell(row=last, column=MONEY_COL, value=sums["money"]).font = Font(bold=True)
    for col in range(1, len(COLUMNS) + 1):
        ws.cell(row=last, column=col).fill = SUM_FILL
    for i, (_title, _key, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%s" % (get_column_letter(len(COLUMNS)), max(1, ws.max_row - 2))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = "учёт_%s.xlsx" % datetime.now(MSK).strftime("%Y-%m-%d_%H%M")
    return name, buf.getvalue()


CAL_HEAD = [
    ("Контрагент", 26),
    ("Артикул", 20),
    ("Наименование", 34),
    ("Литраж, л", 10),
    ("Тариф", 9),
    ("Принято", 11),
    ("Итого, ₽", 12),
]


def build_calendar(client_id=None, query="", date_from=None, date_to=None):
    """Сетка как в таблице Глеба: строка — позиция, колонка — сутки.

    Два блока: остаток на утро каждого дня и деньги за этот день.
    """
    from account import calendar

    data = calendar(client_id=client_id, query=query, date_from=date_from, date_to=date_to)
    days = data["days"]
    columns = CAL_HEAD + [(d[8:10] + "." + d[5:7], 9) for d in days]
    wb, ws = _new_sheet("Хранение", columns)

    def block(title, pick):
        ws.append([title])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for row in data["rows"]:
            head = [
                excel_text(row["client"]),
                excel_text(row["article"]),
                excel_text(row["name"]),
                row["liters"],
                row["tariff"],
                excel_text(row["received"]),
                row["sum"],
            ]
            ws.append(head + [pick(cell) for cell in row["cells"]])
        ws.append([])

    block("Остаток на утро, шт", lambda c: c["qty"])
    block("Стоимость хранения, ₽", lambda c: c["sum"])
    block("Отгружено за день, шт", lambda c: c["ship"] or None)

    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Итого за %s — %s" % (data["from"], data["to"])).font = Font(bold=True)
    ws.cell(row=last, column=7, value=data["total"]).font = Font(bold=True)
    for i, day in enumerate(data["per_day"]):
        cell = ws.cell(row=last, column=len(CAL_HEAD) + 1 + i, value=day["sum"])
        cell.font = Font(bold=True)
    for col in range(1, len(columns) + 1):
        ws.cell(row=last, column=col).fill = SUM_FILL
    if not data["rows"]:
        ws.append(["Нет позиций за период"])
    name = "хранение_%s_%s.xlsx" % (data["from"], data["to"])
    return _finish(wb, ws, columns, name)


def _stamp():
    return datetime.now(MSK).strftime("%Y-%m-%d_%H%M")


def _file_part(name):
    text = "".join(ch if ch.isalnum() or ch in " ._-—" else "_" for ch in str(name or ""))
    text = " ".join(text.split())
    return (text or "клиент")[:40]


def _new_sheet(title, columns):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append([title for title, _w in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    return wb, ws


def _finish(wb, ws, columns, name):
    for i, (_title, width) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return name, buf.getvalue()


def build_marks(ids):
    marks = list_shipment_marks(ids)
    columns = [("Код маркировки", 56)]
    wb, ws = _new_sheet("Коды маркировки", columns)
    seen = set()
    n = 0
    for row in marks:
        code = excel_text(row["code"]).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        ws.append([code])
        n += 1
    if n == 0:
        ws.append(["Нет кодов маркировки в выбранных отгрузках"])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Кодов: %s" % n).font = Font(bold=True)
    return _finish(wb, ws, columns, "коды_маркировки_%s.xlsx" % _stamp())


SHIP_COLUMNS = [
    ("Контрагент", 28),
    ("Дата", 12),
    ("МП", 8),
    ("Схема", 8),
    ("Отправление", 22),
    ("Статус", 22),
    ("Артикул", 22),
    ("Штрихкод", 16),
    ("Наименование", 36),
    ("Шт", 8),
    ("Кодов маркировки", 16),
]


def build_ships(ids):
    ships = get_shipments_by_ids(ids)
    wb, ws = _new_sheet("Отгрузки", SHIP_COLUMNS)
    qty = 0
    marks = 0
    for ship in ships:
        qty += float(ship["qty"] or 0)
        marks += int(ship["marks_count"] or 0)
        ws.append(
            [
                excel_text(ship["client_name"]),
                excel_text((ship["shipped_at"] or "")[:10]),
                excel_text((ship["marketplace"] or "").upper()),
                excel_text((ship["kind"] or "").upper()),
                excel_text(ship["ext_id"]),
                excel_text(status_label(ship["status"])),
                excel_text(ship["article"] or ""),
                excel_text(ship["barcode"] or ""),
                excel_text(ship["name"] or ""),
                ship["qty"],
                ship["marks_count"],
            ]
        )
        url = order_app_url(ship["ms_order_id"])
        if url:
            cell = ws.cell(row=ws.max_row, column=5)
            cell.hyperlink = url
            cell.font = Font(color="005BFF", underline="single")
    if not ships:
        ws.append(["Нет отправлений в выборке"])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Отправлений: %s · шт: %s · кодов: %s" % (len(ships), int(qty), marks)).font = Font(bold=True)
    who = ships[0]["client_name"] if len({s["client_name"] for s in ships}) == 1 else "клиенту"
    return _finish(wb, ws, SHIP_COLUMNS, "отгрузки_%s_%s.xlsx" % (_file_part(who), _stamp()))


CLIENT_STOCK_COLUMNS = [
    ("Контрагент", 28),
    ("Артикул", 22),
    ("Штрихкод", 16),
    ("GTIN", 16),
    ("Наименование", 40),
    ("Тип продукции", 16),
    ("Литраж, л", 11),
    ("Остаток, шт", 12),
    ("Объём, л", 11),
    ("Принято", 12),
    ("Дней на складе", 14),
]


def build_client_stock(ids):
    wanted = [int(x) for x in ids]
    order = {lid: i for i, lid in enumerate(wanted)}
    rows = [r for r in lot_rows(only_open=False) if r["id"] in order]
    rows.sort(key=lambda r: order.get(r["id"], 0))
    wb, ws = _new_sheet("Остаток", CLIENT_STOCK_COLUMNS)
    qty = 0
    volume = 0
    for row in rows:
        qty += float(row["qty"] or 0)
        volume += float(row["volume"] or 0)
        ws.append(
            [
                excel_text(row["client"]),
                excel_text(row["article"]),
                excel_text(row["barcode"]),
                excel_text(row["gtin"]),
                excel_text(row["name"]),
                excel_text(row["tracking"]),
                row["liters"],
                row["qty"],
                row["volume"],
                excel_text(row["received"]),
                row["days"],
            ]
        )
    if not rows:
        ws.append(["Нет выбранных позиций"])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Позиций: %s" % len(rows)).font = Font(bold=True)
    ws.cell(row=last, column=8, value=qty).font = Font(bold=True)
    ws.cell(row=last, column=9, value=round(volume, 2)).font = Font(bold=True)
    who = rows[0]["client"] if len({r["client"] for r in rows}) == 1 else "клиенту"
    return _finish(wb, ws, CLIENT_STOCK_COLUMNS, "остаток_%s_%s.xlsx" % (_file_part(who), _stamp()))
