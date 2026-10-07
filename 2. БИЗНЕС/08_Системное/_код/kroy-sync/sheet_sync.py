"""Таблица приёмки кроя. Новая строка сама становится приёмкой и списанием рулона.

На площадки не ходит. SYNC_ENABLED здесь не читается.
"""

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import config
import core
import cut
import ms_api

SHEET_ID = "1FaVgAGM_RIDcy6PGl0lYIGQuTsQG3H2Fqnf0lHLAiUA"
FACT_SHEET = "Приёмка"
MAP_SHEET = "Сопоставление"
HEADER = [
    "Дата",
    "Изделие",
    "S",
    "M",
    "L",
    "Рулонов",
    "Цвет",
    "Списать рулон",
    "Было рулонов",
    "Статус",
    "Приёмка",
    "Списание",
    "Ключ",
]
NOTE = (
    "Добавь строку: дата, изделие, размеры и сколько рулонов ушло. "
    "Цвет и рулон посчитаются сами. Через несколько секунд появится приёмка изделий "
    "и списание этого рулона в Моём Складе. Проведённую строку не пересчитываем, для правки нужна новая."
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_token = {"value": "", "until": 0}


def sheet_id():
    return os.environ.get("SHEET_ID", "").strip() or SHEET_ID


def iso_date(value):
    """Дата из ячейки в ГГГГ-ММ-ДД. Пусто, если разобрать нельзя."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        day = datetime(1899, 12, 30) + timedelta(days=int(value))
        return day.date().isoformat()
    text = str(value).strip()
    for fmt, chunk in (("%Y-%m-%d", text[:10]), ("%d.%m.%Y", text), ("%d.%m.%y", text)):
        try:
            return datetime.strptime(chunk, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def whole(value):
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value == "":
            return 0
    number = float(value)
    if number < 0 or number != int(number):
        raise ValueError("нужно целое число")
    return int(number)


def content_digest(date, product, sizes, rolls):
    raw = core.fact_payload(date, product, sizes, rolls)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def row_key(spreadsheet_id, row_number, digest):
    raw = "%s|%s|%s" % (spreadsheet_id, row_number, digest)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def next_step(status, stored, digest, ready):
    """skip, ask, wait или post. Повтор той же строки не создаёт второй документ."""
    status = (status or "").strip()
    if status.startswith("проведено"):
        return "skip"
    if not ready:
        return "ask"
    if stored == digest and (status == "проверяю" or status.startswith("ошибка")):
        return "post"
    return "wait"


def _creds_token():
    now = time.time()
    if _token["value"] and _token["until"] > now + 30:
        return _token["value"]
    path = os.environ.get("GOOGLE_SA_PATH", "").strip()
    if not path:
        raise RuntimeError("нет GOOGLE_SA_PATH")
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    creds.refresh(Request())
    _token["value"] = creds.token
    _token["until"] = now + 3000
    return creds.token


def _api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://sheets.googleapis.com/v4/spreadsheets" + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + _creds_token(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("таблица ответила %s: %s" % (exc.code, detail)) from exc


def _meta():
    return _api("GET", "/" + sheet_id() + "?fields=sheets.properties")


def _sheet_id_by_title(title):
    for sheet in _meta().get("sheets") or []:
        props = sheet.get("properties") or {}
        if props.get("title") == title:
            return props.get("sheetId")
    return None


def _values(range_name):
    quoted = urllib.parse.quote(range_name, safe="")
    query = urllib.parse.urlencode({
        "valueRenderOption": "UNFORMATTED_VALUE",
        "majorDimension": "ROWS",
    })
    data = _api("GET", "/%s/values/%s?%s" % (sheet_id(), quoted, query))
    return data.get("values") or []


def _write(range_name, values):
    quoted = urllib.parse.quote(range_name, safe="")
    _api(
        "PUT",
        "/%s/values/%s?valueInputOption=USER_ENTERED" % (sheet_id(), quoted),
        {"range": range_name, "majorDimension": "ROWS", "values": values},
    )


def mapping_rows():
    rows = []
    for product in ms_api.rows("/entity/product"):
        if product.get("pathName") != core.YUJI_FOLDER:
            continue
        name = product.get("name") or ""
        color = core.color_of(name)
        roll = ("Рулон " + color) if color else ""
        article = core.roll_article(color) if color else ""
        rows.append([name, color, roll, article])
    rows.sort(key=lambda item: item[0])
    return rows


def setup():
    """Шапка, формулы и список изделий. Уже введённые строки приёмки не трогает."""
    sheets = _meta().get("sheets") or []
    titles = [(s.get("properties") or {}).get("title") for s in sheets]
    requests = []
    if FACT_SHEET not in titles:
        first = (sheets[0].get("properties") or {}) if sheets else {}
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": first.get("sheetId", 0), "title": FACT_SHEET},
                "fields": "title",
            }
        })
    if MAP_SHEET not in titles:
        requests.append({"addSheet": {"properties": {"title": MAP_SHEET}}})
    if requests:
        _api("POST", "/" + sheet_id() + ":batchUpdate", {"requests": requests})
    fact_id = _sheet_id_by_title(FACT_SHEET)
    map_id = _sheet_id_by_title(MAP_SHEET)
    _write(FACT_SHEET + "!A1", [[NOTE]])
    _write(FACT_SHEET + "!A2:M2", [HEADER])
    _write(FACT_SHEET + "!G3", [[
        '=ARRAYFORMULA(IF(B3:B="";"";IFERROR(VLOOKUP(B3:B;\'Сопоставление\'!A:B;2;FALSE);"")))'
    ]])
    _write(FACT_SHEET + "!H3", [[
        '=ARRAYFORMULA(IF(B3:B="";"";IFERROR(VLOOKUP(B3:B;\'Сопоставление\'!A:C;3;FALSE);"нет такого изделия")))'
    ]])
    catalog = mapping_rows()
    _write(MAP_SHEET + "!A1:D1", [["Изделие", "Цвет", "Рулон", "Артикул"]])
    if catalog:
        _write(MAP_SHEET + "!A2:D%d" % (len(catalog) + 1), catalog)
    end = max(len(catalog) + 1, 2)
    _api("POST", "/" + sheet_id() + ":batchUpdate", {"requests": [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": fact_id, "gridProperties": {"frozenRowCount": 2}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": fact_id, "startRowIndex": 1, "endRowIndex": 2},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": fact_id,
                    "startRowIndex": 2,
                    "endRowIndex": 500,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_RANGE",
                        "values": [{"userEnteredValue": "=%s!A2:A%d" % (MAP_SHEET, end)}],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": fact_id, "dimension": "COLUMNS", "startIndex": 12, "endIndex": 13},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": map_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
    ]})
    return len(catalog)


def _cell(row, index):
    return row[index] if index < len(row) else ""


def _ask_text(date, product, rolls, pieces):
    if not date:
        return "укажи дату"
    if not product:
        return "укажи изделие"
    if pieces < 1:
        return "укажи, сколько штук пришло"
    if rolls < 1:
        return "укажи, сколько рулонов списать"
    return ""


def _paint(row_number, before, status, enter_name, loss_name, digest):
    _write("%s!I%d:M%d" % (FACT_SHEET, row_number, row_number), [[
        "" if before is None else before,
        status,
        enter_name,
        loss_name,
        digest,
    ]])


def process_once():
    """Один проход. Возвращает, сколько строк проведено."""
    rows = _values(FACT_SHEET + "!A3:M")
    posted = 0
    for offset, raw in enumerate(rows):
        row_number = offset + 3
        product = str(_cell(raw, 1) or "").strip()
        if not product:
            continue
        status = str(_cell(raw, 9) or "").strip()
        stored = str(_cell(raw, 12) or "").strip()
        try:
            sizes = {"S": whole(_cell(raw, 2)), "M": whole(_cell(raw, 3)), "L": whole(_cell(raw, 4))}
            rolls = whole(_cell(raw, 5))
        except ValueError:
            if status != "нужны целые числа":
                _paint(row_number, _cell(raw, 8), "нужны целые числа", "", "", stored)
            continue
        date = iso_date(_cell(raw, 0))
        pieces = sum(sizes.values())
        ready = bool(date and product and pieces > 0 and rolls > 0)
        digest = content_digest(date or "-", product, sizes, rolls) if date else ""
        step = next_step(status, stored, digest, ready)
        if step == "skip":
            continue
        if step == "ask":
            hint = _ask_text(date, product, rolls, pieces)
            if hint and hint != status:
                _paint(row_number, _cell(raw, 8), hint, "", "", stored)
            continue
        if step == "wait":
            _paint(row_number, _cell(raw, 8), "проверяю", "", "", digest)
            continue
        color = core.color_of(product)
        key = row_key(sheet_id(), row_number, digest)
        try:
            result = cut.apply_fact(
                date, product, color, sizes, rolls, True,
                external_key=key, force=True,
            )
        except Exception as exc:
            message = "ошибка: %s" % exc
            _paint(row_number, _cell(raw, 8), message[:400], "", "", digest)
            print(message, flush=True)
            continue
        if not result["ok"]:
            _paint(row_number, result["roll_before"], result["text"], "", "", digest)
            print("строка %s: %s" % (row_number, result["text"]), flush=True)
            continue
        before = result["roll_before"]
        after = None if before is None else float(before) - rolls
        text = "проведено. Рулон %s, было %s, стало %s" % (
            result["roll_name"] or color, before, after,
        )
        if "меньше" in result["text"]:
            text += ". На складе рулонов было меньше, чем списали"
        _paint(row_number, before, text, result["enter_name"], result["loss_name"], digest)
        posted += 1
        print("строка %s проведена, приёмка %s, списание %s" % (
            row_number, result["enter_name"], result["loss_name"],
        ), flush=True)
    return posted


def main():
    if "--setup" in sys.argv:
        count = setup()
        print("таблица готова, изделий %s" % count)
        return
    if "--once" in sys.argv:
        print("проведено строк: %s" % process_once())
        return
    pause = int(os.environ.get("POLL_SECONDS", "15") or "15")
    while True:
        try:
            process_once()
        except Exception as exc:
            print("проход не удался: %s" % exc, flush=True)
        time.sleep(pause)


if __name__ == "__main__":
    main()
