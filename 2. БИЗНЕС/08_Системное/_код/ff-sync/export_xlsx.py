"""Выгрузка учёта в Excel по кнопке."""

import io
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from account import lot_rows, totals
from db import catalog_card, find_cache, get_shipments_by_ids, list_shipment_marks, prefer_hit, status_label
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
    ("Ставка хранения, ₽/л/сутки", "rate", 22),
    ("Сборка, ₽/шт", "pick_rate", 13),
    ("Принято, шт", "qty_in", 12),
    ("Отгружено, шт", "shipped", 13),
    ("Остаток, шт", "qty", 12),
    ("Объём, л", "volume", 11),
    ("Заведено", "received", 12),
    ("Приёмка на склад", "accepted", 16),
    ("Дней на складе", "days", 15),
    ("Дней к оплате", "bill_days", 14),
    ("Литро-суток", "liter_days", 13),
    ("К выставлению, ₽", "total", 17),
]

QTY_COL = 13
VOLUME_COL = 14
MONEY_COL = 20

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
    ("Ставка", 9),
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
                row["rate"],
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


MARK_COLUMNS = [
    ("Код маркировки", 56),
    ("Артикул", 22),
    ("GTIN", 16),
    ("Наименование", 40),
]


def _gtin_hint(cache, client_id, barcode, article):
    """GTIN из кэша каталога, когда в самом коде его нет: WB отдаёт УИН без 01."""
    key = (client_id, barcode or "", article or "")
    if key in cache:
        return cache[key]
    found = ""
    hits = find_cache(client_id, barcode=barcode or None, article=article or None) if client_id else []
    for hit in prefer_hit(hits) if hits else []:
        if hit["gtin"]:
            found = hit["gtin"]
            break
    cache[key] = found
    return found


def build_marks(ids):
    """Плоский список: строка на код, рядом артикул, GTIN и наименование."""
    marks = list_shipment_marks(ids)
    wb, ws = _new_sheet("Коды маркировки", MARK_COLUMNS)
    seen = set()
    hints = {}
    n = 0
    for row in marks:
        code = excel_text(row["code"]).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        article = (row["article"] or row["ship_article"] or "").strip()
        barcode = (row["ship_barcode"] or "").strip()
        gtin = (row["gtin"] or "").strip() or _gtin_hint(hints, row["client_id"], barcode, article)
        ws.append([code, excel_text(article), excel_text(gtin), excel_text(row["ship_name"] or "")])
        n += 1
    if n == 0:
        ws.append(["Нет кодов маркировки в выбранных отгрузках"])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Кодов: %s" % n).font = Font(bold=True)
    return _finish(wb, ws, MARK_COLUMNS, "коды_маркировки_%s.xlsx" % _stamp())


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


WEEK_HEAD = [
    ("Артикул", 24),
    ("Штрихкод", 18),
    ("Наименование", 40),
]


def _days_between(day_from, day_to):
    start = datetime.strptime(day_from, "%Y-%m-%d")
    end = datetime.strptime(day_to, "%Y-%m-%d")
    if end < start:
        raise ValueError("конец периода раньше начала")
    out = []
    while start <= end:
        out.append(start.strftime("%Y-%m-%d"))
        start += timedelta(days=1)
    return out


def build_weekly(client, day_from, day_to, rows):
    """Недельный отчёт клиенту: строки — артикулы, столбцы — дни, значения — штуки.

    Формат из созвона с Сергеем: он сейчас выгружает это из ЛК WB и переносит в
    таблицу Димы. Раскладку столбцов до отправки клиенту надо сверить с Димой —
    таблицу ведёт он.
    """
    days = _days_between(day_from, day_to)
    columns = WEEK_HEAD + [(d[8:10] + "." + d[5:7], 9) for d in days] + [("Итого", 10)]
    wb, ws = _new_sheet("Отгружено", columns)
    bag = {}
    for row in rows:
        key = (row["article"] or "", row["barcode"] or "")
        item = bag.setdefault(key, {"name": row["name"] or "", "days": {}})
        item["days"][row["day"]] = item["days"].get(row["day"], 0) + float(row["qty"] or 0)
        if not item["name"]:
            item["name"] = row["name"] or ""
    per_day = {d: 0.0 for d in days}
    total = 0.0
    for (article, barcode), item in sorted(bag.items(), key=lambda kv: kv[0][0].lower()):
        line = [excel_text(article), excel_text(barcode), excel_text(item["name"])]
        row_sum = 0.0
        for day in days:
            got = item["days"].get(day, 0)
            per_day[day] += got
            row_sum += got
            line.append(int(got) if got and float(got).is_integer() else (got or None))
        total += row_sum
        line.append(int(row_sum) if float(row_sum).is_integer() else round(row_sum, 3))
        ws.append(line)
    if not bag:
        ws.append(["За период %s — %s отгрузок нет" % (day_from, day_to)])
    last = ws.max_row + 1
    # строка на пару артикул + штрихкод: у WB один артикул держит все размеры,
    # и склеивать их в одну строку нельзя — клиент считает по размерам
    ws.cell(row=last, column=1, value="Позиций: %s" % len(bag)).font = Font(bold=True)
    for i, day in enumerate(days):
        cell = ws.cell(row=last, column=len(WEEK_HEAD) + 1 + i, value=int(per_day[day]) or None)
        cell.font = Font(bold=True)
    ws.cell(row=last, column=len(columns), value=int(total) if float(total).is_integer() else round(total, 3)).font = Font(bold=True)
    for col in range(1, len(columns) + 1):
        ws.cell(row=last, column=col).fill = SUM_FILL
    name = "отгружено_%s_%s_%s.xlsx" % (_file_part(client), day_from, day_to)
    return _finish(wb, ws, columns, name)


PICK_COLUMNS = [
    ("Артикул", 24),
    ("Размер", 10),
    ("Штрихкод", 18),
    ("Наименование", 44),
    ("Взять, шт", 11),
    ("Заданий", 10),
    ("Площадка", 10),
]


def build_picking(rows, who=""):
    """Лист подбора: что и сколько взять со склада под текущую выборку.

    Строка — не отправление, а позиция: сборщик идёт по стеллажам за товаром, и
    ему важно суммарное количество по артикулу, а не список номеров. Размер
    добираем из каталога — в самом отправлении его нет, а у WB один артикул
    держит все размеры и без размера позиция не находится на полке.
    """
    bag = {}
    cards = {}
    for row in rows:
        article = (row["article"] or "").strip()
        barcode = (row["barcode"] or "").strip()
        key = (row["client_id"], article, barcode)
        if key not in cards:
            cards[key] = catalog_card(row["client_id"], barcode=barcode, article=article)
        card = cards[key]
        code = barcode or card.get("barcode") or ""
        size = card.get("size") or ""
        # у безразмерных товаров WB отдаёт размер «0», в колонке он только мешает
        if size == "0":
            size = ""
        slot = (article.lower(), size, code)
        item = bag.setdefault(
            slot,
            {
                "article": article,
                "size": size,
                "barcode": code,
                "name": (row["name"] or "").strip() or card.get("name") or "",
                "qty": 0.0,
                "orders": 0,
                "mp": set(),
            },
        )
        item["qty"] += float(row["qty"] or 0)
        item["orders"] += 1
        item["mp"].add((row["marketplace"] or "").upper())
    items = sorted(bag.values(), key=lambda x: (x["article"].lower(), x["size"]))
    wb, ws = _new_sheet("Лист подбора", PICK_COLUMNS)
    qty = 0.0
    for item in items:
        qty += item["qty"]
        ws.append(
            [
                excel_text(item["article"]),
                excel_text(item["size"]),
                excel_text(item["barcode"]),
                excel_text(item["name"]),
                int(item["qty"]) if float(item["qty"]).is_integer() else item["qty"],
                item["orders"],
                excel_text(" / ".join(sorted(item["mp"]))),
            ]
        )
    if not items:
        ws.append(["В выборке нет отправлений: проверь контрагента, смену и вкладку"])
    last = ws.max_row + 1
    ws.cell(row=last, column=1, value="Позиций: %s" % len(items)).font = Font(bold=True)
    ws.cell(row=last, column=5, value=int(qty) if float(qty).is_integer() else round(qty, 3)).font = Font(bold=True)
    ws.cell(row=last, column=6, value=sum(i["orders"] for i in items)).font = Font(bold=True)
    for col in range(1, len(PICK_COLUMNS) + 1):
        ws.cell(row=last, column=col).fill = SUM_FILL
    return _finish(wb, ws, PICK_COLUMNS, "лист_подбора_%s_%s.xlsx" % (_file_part(who), _stamp()))


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
    ("Приёмка на склад", 16),
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
                excel_text(row["accepted"] or "ждёт приёмки"),
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
