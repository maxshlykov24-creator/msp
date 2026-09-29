"""График Кристины и Тани. Одна галочка в день, лист «График»."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lib

TZ = ZoneInfo("Europe/Moscow")
SHEET_ID = "17V1PO2k4BjFuOQPuXmXvq8ckvOmU8nqD9Q4YhusIwdM"
TAB = "График"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
EPOCH = date(1899, 12, 30)
NOTE = (
    "Одна галочка в день, кто работает. "
    "На неё приходят новые заявки и открытые задачи. "
    "Две галочки или пусто: в этот день никого не переключаем."
)
_CACHE: dict = {"at": 0.0, "rows": {}}


def sa_path() -> Path | None:
    env = lib.load_env()
    candidates = [
        os.environ.get("GOOGLE_SA_PATH") or "",
        env.get("GOOGLE_SA_PATH") or "",
        "/opt/2my/google_sa.json",
        str(lib.CODE / "google_sa.json"),
        "/Users/max/CURSOR/2. БИЗНЕС/04_Производство/Активные/MANSBAND/_private/google_sheets_sa.json",
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return Path(raw)
    return None


def _serial(day: date) -> int:
    return (day - EPOCH).days


def _day(value) -> date | None:
    if isinstance(value, (int, float)) and value > 20000:
        return EPOCH + timedelta(days=int(value))
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue
    return None


def _on(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().casefold() in ("true", "да", "1", "yes"):
        return True
    return False


def _token() -> str:
    path = sa_path()
    if not path:
        raise RuntimeError("Нет файла ключа Google")
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def _a1(cell: str) -> str:
    return urllib.parse.quote(f"{TAB}!{cell}", safe="")


def _api(method: str, url: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _rows(force: bool = False) -> dict[date, tuple[bool, bool]]:
    now = time.time()
    if not force and now - float(_CACHE["at"]) < 60 and _CACHE["rows"]:
        return _CACHE["rows"]
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
        f"/values/{_a1('A3:D')}?valueRenderOption=UNFORMATTED_VALUE"
    )
    data = _api("GET", url)
    out: dict[date, tuple[bool, bool]] = {}
    for row in data.get("values") or []:
        day = _day(row[0] if row else None)
        if not day:
            continue
        kristina = _on(row[2]) if len(row) > 2 else False
        tanya = _on(row[3]) if len(row) > 3 else False
        out[day] = (kristina, tanya)
    _CACHE["at"] = now
    _CACHE["rows"] = out
    return out


def duty(day: date | None = None) -> tuple[int | None, str]:
    """Кто работает в этот день. Пусто или две галочки: никого не назначаем."""
    day = day or datetime.now(TZ).date()
    try:
        flags = _rows().get(day)
    except Exception as exc:
        return None, f"таблица недоступна: {exc}"
    if flags is None:
        return None, "нет строки на эту дату"
    kristina, tanya = flags
    if kristina and not tanya:
        return lib.USER_KRISTINA, "Кристина"
    if tanya and not kristina:
        return lib.USER_TANYA, "Таня"
    if kristina and tanya:
        return None, "две галочки"
    return None, "пусто"


def other(user_id: int | None) -> int | None:
    if user_id == lib.USER_KRISTINA:
        return lib.USER_TANYA
    if user_id == lib.USER_TANYA:
        return lib.USER_KRISTINA
    return None


def _last_day() -> date | None:
    rows = _rows(force=True)
    return max(rows) if rows else None


def ensure_horizon(days_ahead: int = 21, extend_to: int = 90) -> int:
    """Если календарь кончается раньше чем через 3 недели, дописывает дни."""
    last = _last_day()
    today = datetime.now(TZ).date()
    if last and last >= today + timedelta(days=days_ahead):
        return 0
    start = (last + timedelta(days=1)) if last else today
    end = today + timedelta(days=extend_to)
    if start > end:
        return 0
    values = []
    day = start
    while day <= end:
        values.append([_serial(day), WD[day.weekday()], False, False])
        day += timedelta(days=1)
    if not values:
        return 0
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
        f"/values/{_a1('A3:D')}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )
    _api("POST", url, {"values": values})
    _CACHE["at"] = 0
    return len(values)


def publish() -> int:
    """Пустой календарь с галочками. Уже стоящие галочки не затирает."""
    try:
        existing = _rows(force=True)
    except Exception:
        existing = {}
    if any(pair[0] or pair[1] for pair in existing.values()):
        raise SystemExit("В графике уже есть галочки, лист не переписываю")
    start = datetime.now(TZ).date()
    end = date(start.year, 12, 31)
    if end < start + timedelta(days=60):
        end = start + timedelta(days=90)
    days = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    values = [[_serial(day), WD[day.weekday()], False, False] for day in days]
    meta = _api(
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}?fields=sheets(properties(sheetId,title))",
    )
    sheet_id = 0
    for sheet in meta.get("sheets") or []:
        props = sheet.get("properties") or {}
        if props.get("title") in (TAB, "Лист1"):
            sheet_id = props.get("sheetId") or 0
            break
    _api(
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
        {"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "title": TAB},
                "fields": "title",
            }
        }]},
    )
    _api(
        "PUT",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{_a1('A1:D2')}?valueInputOption=RAW",
        {"values": [[NOTE], ["Дата", "День", "Кристина", "Таня"]]},
    )
    end_row = 2 + len(values)
    _api(
        "PUT",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{_a1(f'A3:D{end_row}')}?valueInputOption=RAW",
        {"values": values},
    )
    weekend = []
    today_idx = None
    for pos, day in enumerate(days):
        if day == start:
            today_idx = 2 + pos
        if day.weekday() >= 5:
            weekend.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2 + pos,
                        "endRowIndex": 3 + pos,
                        "startColumnIndex": 0,
                        "endColumnIndex": 2,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95}}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
    requests = [
        {
            "updateSpreadsheetProperties": {
                "properties": {"locale": "ru_RU", "timeZone": "Europe/Moscow"},
                "fields": "locale,timeZone",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "title": TAB,
                    "gridProperties": {"frozenRowCount": 2},
                },
                "fields": "title,gridProperties.frozenRowCount",
            }
        },
        {
            "mergeCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
                "cell": {
                    "userEnteredFormat": {
                        "wrapStrategy": "WRAP",
                        "verticalAlignment": "MIDDLE",
                        "backgroundColor": {"red": 0.97, "green": 0.96, "blue": 0.94},
                        "textFormat": {"fontSize": 11},
                    }
                },
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,backgroundColor,textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 4},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.93, "green": 0.91, "blue": 0.87},
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": end_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": end_row,
                    "startColumnIndex": 2,
                    "endColumnIndex": 4,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": end_row,
                    "startColumnIndex": 2,
                    "endColumnIndex": 4,
                },
                "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True},
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 120},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                "properties": {"pixelSize": 56},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 4},
                "properties": {"pixelSize": 130},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 26},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 48},
                "fields": "pixelSize",
            }
        },
    ]
    requests.extend(weekend)
    if today_idx is not None:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": today_idx,
                    "endRowIndex": today_idx + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 0.91, "blue": 0.64}}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
    _api(
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
        {"requests": requests},
    )
    _CACHE["at"] = 0
    return len(days)
