import os

import gspread
from google.oauth2.service_account import Credentials

from ms import TRACKING_LABELS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEETS = ("Приёмка", "Учёт")
DROP_SHEETS = (
    "Клиенты",
    "Вход",
    "Пульт",
    "Каталог",
    "Требует решения",
    "Хранение",
    "Отгрузки",
    "Кизы",
    "Sheet1",
    "Лист1",
)

INTAKE_HINT = (
    "Выбери клиента, впиши штрихкод, количество, литраж и тип продукции. "
    "Серое заполнит система. Меню Фулфилмент → Создать товары и заказ поставщика."
)
INTAKE_HEADER = [
    "Клиент",
    "Штрихкод",
    "Количество",
    "Литраж_л",
    "Тип продукции",
    "Артикул",
    "Название",
    "Площадка",
    "GTIN",
    "Товар в МС",
    "Статус",
]
INTAKE_DATA_START = 3
INTAKE_INPUT_COLS = 5

UCHET_HINT = "Партии на складе. Остаток списывается с самой старой партии. Фильтры в шапке."
UCHET_HEADER = [
    "Контрагент",
    "Артикул",
    "Штрихкод",
    "GTIN",
    "Название",
    "Тип продукции",
    "Литраж ед.",
    "Остаток шт",
    "Литров всего",
    "Дата поступления",
    "Дней на складе",
    "Хранение руб",
    "Приёмка руб",
    "Отгрузка руб",
    "Итого руб",
]

WHITE = {"red": 1, "green": 1, "blue": 1}
GRAY = {"red": 0.91, "green": 0.91, "blue": 0.91}
HINT_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}
INPUT_HEAD = {"red": 0.85, "green": 0.92, "blue": 0.83}
AUTO_HEAD = {"red": 0.82, "green": 0.82, "blue": 0.82}
YELLOW = {"red": 1, "green": 0.95, "blue": 0.7}
MUTED = {"red": 0.4, "green": 0.4, "blue": 0.4}
BLACK = {"red": 0.15, "green": 0.15, "blue": 0.15}


def sa_path():
    return os.environ.get("GOOGLE_SA_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "google_sa.json"
    )


def sheet_id():
    val = os.environ.get("SHEET_ID")
    if not val:
        raise SystemExit("нет SHEET_ID")
    return val


def client():
    creds = Credentials.from_service_account_file(sa_path(), scopes=SCOPES)
    return gspread.authorize(creds)


def open_book():
    return client().open_by_key(sheet_id())


def sheet_id_num(ws):
    return ws.id


def rgb_cell(bg, fg=None, bold=False, wrap=False):
    fmt = {
        "userEnteredFormat": {
            "backgroundColor": bg,
            "textFormat": {"foregroundColor": fg or BLACK, "bold": bold, "fontSize": 10},
            "verticalAlignment": "MIDDLE",
        }
    }
    if wrap:
        fmt["userEnteredFormat"]["wrapStrategy"] = "WRAP"
    return fmt


def repeat(ws, r1, r2, c1, c2, bg, fg=None, bold=False, wrap=False):
    cell = rgb_cell(bg, fg, bold, wrap)
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id_num(ws),
                "startRowIndex": r1,
                "endRowIndex": r2,
                "startColumnIndex": c1,
                "endColumnIndex": c2,
            },
            "cell": cell,
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }
    }


def unmerge(ws, r1, r2, c1, c2):
    return {
        "unmergeCells": {
            "range": {
                "sheetId": sheet_id_num(ws),
                "startRowIndex": r1,
                "endRowIndex": r2,
                "startColumnIndex": c1,
                "endColumnIndex": c2,
            }
        }
    }


def merge(ws, r1, r2, c1, c2):
    return {
        "mergeCells": {
            "range": {
                "sheetId": sheet_id_num(ws),
                "startRowIndex": r1,
                "endRowIndex": r2,
                "startColumnIndex": c1,
                "endColumnIndex": c2,
            },
            "mergeType": "MERGE_ALL",
        }
    }


def widths(ws, pairs):
    reqs = []
    for col, px in pairs:
        reqs.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id_num(ws),
                        "dimension": "COLUMNS",
                        "startIndex": col,
                        "endIndex": col + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
    return reqs


def dropdown_list(ws, r1, r2, c1, values):
    if not values:
        return None
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id_num(ws),
                "startRowIndex": r1,
                "endRowIndex": r2,
                "startColumnIndex": c1,
                "endColumnIndex": c1 + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": str(v)} for v in values],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }
    }


def basic_filter(ws, r1, r2, c1, c2):
    return {
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": sheet_id_num(ws),
                    "startRowIndex": r1,
                    "endRowIndex": r2,
                    "startColumnIndex": c1,
                    "endColumnIndex": c2,
                }
            }
        }
    }


def client_names():
    from db import list_clients

    return [c["name"] for c in list_clients() if c["active"]]


def ensure_sheets(book):
    have = {ws.title: ws for ws in book.worksheets()}
    if "Вход" in have and "Приёмка" not in have:
        have["Вход"].update_title("Приёмка")
        have["Приёмка"] = have.pop("Вход")
        print("лист Вход переименован в Приёмка")
    for title in SHEETS:
        if title in have:
            continue
        book.add_worksheet(title=title, rows=2000, cols=16)
        print("создан лист %s" % title)
        have = {ws.title: ws for ws in book.worksheets()}
    for title in list(have):
        if title in SHEETS:
            continue
        if title not in DROP_SHEETS and title not in ("Вход",):
            continue
        if len(book.worksheets()) <= 1:
            break
        try:
            book.del_worksheet(have[title])
            print("лист %s удалён" % title)
        except Exception as exc:
            print("не удалил %s: %s" % (title, exc))
    have = {ws.title: ws for ws in book.worksheets()}
    try:
        book.reorder_worksheets([have[t] for t in SHEETS if t in have])
    except Exception as exc:
        print("порядок листов: %s" % exc)
    return {ws.title: ws for ws in book.worksheets()}


def _has_hint(ws, text_start):
    first = (ws.acell("A1").value or "").strip()
    return first.startswith(text_start)


def _migrate_intake(ws):
    values = ws.get_all_values()
    if not values:
        return
    first = str(values[0][0] if values[0] else "")
    if first.startswith("Выбери") or first.startswith("Шаг"):
        header = values[1] if len(values) > 1 else []
        body = values[2:]
    else:
        header = values[0]
        body = values[1:]
    old = ["Клиент", "Штрихкод", "Литраж_л", "Артикул", "Название", "Площадка", "Статус"]
    if list(header[:7]) != old:
        return
    rows = [[INTAKE_HINT], INTAKE_HEADER]
    for line in body:
        line = list(line) + [""] * 8
        rows.append(
            [line[0], line[1], "1", line[2], "Не маркируется", line[3], line[4], line[5], "", "", line[6]]
        )
    ws.clear()
    ws.update(rows, "A1", value_input_option="RAW")
    print("лист Приёмка: колонки под новый порядок")


def setup_priemka(book, names=None):
    ws = {s.title: s for s in book.worksheets()}["Приёмка"]
    if ws.row_count < 2000:
        ws.resize(rows=2000, cols=12)
    _migrate_intake(ws)
    ws.update([[INTAKE_HINT], INTAKE_HEADER], "A1", value_input_option="RAW")
    ws.freeze(rows=2)
    names = names if names is not None else client_names()
    reqs = [
        unmerge(ws, 0, 1, 0, 11),
        merge(ws, 0, 1, 0, 11),
        repeat(ws, 0, 1, 0, 11, HINT_BG, MUTED, False, True),
        repeat(ws, 1, 2, 0, INTAKE_INPUT_COLS, INPUT_HEAD, BLACK, True),
        repeat(ws, 1, 2, INTAKE_INPUT_COLS, 11, AUTO_HEAD, MUTED, True),
        repeat(ws, 2, 2000, 0, INTAKE_INPUT_COLS, WHITE, BLACK),
        repeat(ws, 2, 2000, INTAKE_INPUT_COLS, 11, GRAY, MUTED),
        dropdown_list(ws, 2, 2000, 4, TRACKING_LABELS),
    ]
    dd = dropdown_list(ws, 2, 2000, 0, names or ["—"])
    if dd:
        reqs.append(dd)
    reqs += widths(
        ws,
        [
            (0, 220),
            (1, 150),
            (2, 90),
            (3, 80),
            (4, 140),
            (5, 140),
            (6, 260),
            (7, 90),
            (8, 140),
            (9, 160),
            (10, 280),
        ],
    )
    ws.spreadsheet.batch_update({"requests": [r for r in reqs if r]})
    print("лист Приёмка готов")
    return ws


def setup_uchet(book):
    ws = {s.title: s for s in book.worksheets()}["Учёт"]
    if ws.row_count < 2000:
        ws.resize(rows=2000, cols=16)
    hint = (ws.acell("A1").value or "").strip()
    if not hint.startswith("Партии") and not hint.startswith("На складе"):
        ws.update([[UCHET_HINT], UCHET_HEADER], "A1", value_input_option="RAW")
    else:
        ws.update([UCHET_HEADER], "A2", value_input_option="RAW")
    ws.freeze(rows=2)
    reqs = [
        unmerge(ws, 0, 1, 0, 15),
        merge(ws, 0, 1, 0, 15),
        repeat(ws, 0, 1, 0, 15, HINT_BG, MUTED, False, True),
        repeat(ws, 1, 2, 0, 15, AUTO_HEAD, BLACK, True),
        repeat(ws, 2, 2000, 0, 15, WHITE, BLACK),
        basic_filter(ws, 1, 2000, 0, 15),
    ] + widths(
        ws,
        [
            (0, 200),
            (1, 140),
            (2, 140),
            (3, 140),
            (4, 240),
            (5, 120),
            (6, 90),
            (7, 90),
            (8, 100),
            (9, 140),
            (10, 110),
            (11, 110),
            (12, 110),
            (13, 110),
            (14, 110),
        ],
    )
    ws.spreadsheet.batch_update({"requests": reqs})
    print("лист Учёт готов")
    return ws


def write_uchet(book, rows, pcs, liters_all, money_all):
    ws = book.worksheet("Учёт")
    hint = "На складе %s шт, %s л, к оплате %s руб. Остаток списывается с самой старой партии." % (
        pcs,
        liters_all,
        money_all,
    )
    empty = [[""] * len(UCHET_HEADER)]
    values = [[hint], UCHET_HEADER]
    if rows:
        values.extend(rows)
    else:
        values.extend(empty)
    end = max(len(values), 50)
    pad = end - len(values)
    if pad > 0:
        values.extend([[""] * len(UCHET_HEADER)] * pad)
    ws.update(values, "A1", value_input_option="RAW")
    ws.freeze(rows=2)
    ws.spreadsheet.batch_update(
        {
            "requests": [
                unmerge(ws, 0, 1, 0, 15),
                merge(ws, 0, 1, 0, 15),
                repeat(ws, 0, 1, 0, 15, HINT_BG, MUTED, False, True),
                basic_filter(ws, 1, max(len(rows) + 2, 3), 0, 15),
            ]
        }
    )


def paint_cells(ws, jobs):
    reqs = [repeat(ws, r, r + 1, c, c + 1, bg, BLACK) for r, c, bg in jobs]
    if reqs:
        ws.spreadsheet.batch_update({"requests": reqs})


def refresh_client_dropdown(book=None):
    if book is None:
        book = open_book()
    ensure_sheets(book)
    if "Приёмка" not in {s.title for s in book.worksheets()}:
        return
    setup_priemka(book)


def setup_all(book=None):
    if book is None:
        book = open_book()
    ensure_sheets(book)
    setup_priemka(book)
    setup_uchet(book)
    return book


def access_ok():
    book = open_book()
    titles = [ws.title for ws in book.worksheets()]
    return book.title, titles
