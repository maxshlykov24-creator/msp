"""Выгрузка учёта в Excel по кнопке."""

import io
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from account import lot_rows, totals

MSK = timezone(timedelta(hours=3))

COLUMNS = [
    ("Контрагент", "client", 26),
    ("Артикул", "article", 22),
    ("Штрихкод", "barcode", 16),
    ("GTIN", "gtin", 16),
    ("Наименование", "name", 40),
    ("Тип продукции", "tracking", 16),
    ("Литраж, л", "liters", 11),
    ("Остаток, шт", "qty", 12),
    ("Объём, л", "volume", 11),
    ("Принято", "received", 12),
    ("Дней на складе", "days", 15),
    ("Хранение, ₽", "storage", 13),
    ("Приёмка, ₽", "intake", 12),
    ("Отгрузка, ₽", "ship", 13),
    ("К выставлению, ₽", "total", 17),
]

HEAD_FILL = PatternFill("solid", fgColor="005BFF")
SUM_FILL = PatternFill("solid", fgColor="EEF3FA")


def build(client_id=None, query=""):
    rows = lot_rows(client_id=client_id, query=query)
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
    ws.cell(row=last, column=8, value=sums["qty"]).font = Font(bold=True)
    ws.cell(row=last, column=9, value=sums["volume"]).font = Font(bold=True)
    ws.cell(row=last, column=15, value=sums["money"]).font = Font(bold=True)
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
