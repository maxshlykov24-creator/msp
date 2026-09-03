"""Google Sheets: только лист «Данные». Sheet1 не трогаем."""
from __future__ import annotations

import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings
from app.map_row import HEADER

log = logging.getLogger("sheets")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def qid(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        settings.google_sa_path, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _meta(svc) -> dict[str, Any]:
    return (
        svc.spreadsheets()
        .get(
            spreadsheetId=settings.spreadsheet_id,
            fields="properties(title),sheets(properties(sheetId,title))",
        )
        .execute()
    )


def sheet_map(svc) -> dict[str, int]:
    meta = _meta(svc)
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def ensure_data_sheet(svc) -> int:
    titles = sheet_map(svc)
    if settings.data_sheet in titles:
        return titles[settings.data_sheet]
    resp = (
        svc.spreadsheets()
        .batchUpdate(
            spreadsheetId=settings.spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": settings.data_sheet,
                                "gridProperties": {"rowCount": 2000, "columnCount": 15, "frozenRowCount": 1},
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )
    sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    log.info("создан лист %s id=%s", settings.data_sheet, sid)
    return sid


def get_values(svc, title: str, rng: str = "A1:O") -> list[list[Any]]:
    resp = (
        svc.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{qid(title)}!{rng}",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return resp.get("values", [])


def write_dannye(svc, rows: list[list[str]]) -> None:
    """Сначала header+N строк, потом clear хвоста. Не clear→update."""
    ensure_data_sheet(svc)
    n = len(rows)
    values = [list(HEADER)] + rows
    svc.spreadsheets().values().update(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(settings.data_sheet)}!A1",
        valueInputOption=settings.sheets_value_input,
        body={"values": values},
    ).execute()
    tail_from = n + 2
    svc.spreadsheets().values().clear(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(settings.data_sheet)}!A{tail_from}:O",
        body={},
    ).execute()
    log.info("Данные: записано %s строк, хвост с A%s очищен", n, tail_from)


def duplicate_sheet(svc, source_title: str, dest_title: str) -> int:
    titles = sheet_map(svc)
    if source_title not in titles:
        raise RuntimeError(f"нет листа {source_title}")
    if dest_title in titles:
        log.info("бэкап %s уже есть", dest_title)
        return titles[dest_title]
    resp = (
        svc.spreadsheets()
        .batchUpdate(
            spreadsheetId=settings.spreadsheet_id,
            body={
                "requests": [
                    {
                        "duplicateSheet": {
                            "sourceSheetId": titles[source_title],
                            "newSheetName": dest_title,
                        }
                    }
                ]
            },
        )
        .execute()
    )
    return resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]


def set_sheet1_filter(svc, formula: str) -> None:
    """A1 — статическая шапка (сейчас там QUERY, его надо снять, иначе spill столкнётся).
    A2 — FILTER. Имена/порядок колонок как у контракта HEADER."""
    title = settings.sheet1_name
    svc.spreadsheets().values().clear(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A1:O",
        body={},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A1",
        valueInputOption="RAW",
        body={"values": [list(HEADER)]},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A2",
        valueInputOption="USER_ENTERED",
        body={"values": [[formula]]},
    ).execute()
    log.info("Sheet1 A1=шапка, A2=%s", formula)


def access_ok() -> tuple[bool, str]:
    try:
        svc = sheets_service()
        meta = _meta(svc)
        title = meta.get("properties", {}).get("title", "")
        sheets = [s["properties"]["title"] for s in meta.get("sheets", [])]
        return True, f"{title}: {sheets}"
    except HttpError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)
