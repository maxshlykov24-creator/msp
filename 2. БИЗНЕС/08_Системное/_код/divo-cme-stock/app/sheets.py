"""Google Sheets: листы «Данные» и «Склад». Sheet1 и «Пометки» не трогаем."""
from __future__ import annotations

import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings
from app.map_row import CORE_HEADER, HEADER, LAST_COL, LAST_CORE_COL

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
            fields="properties(title),sheets(properties(sheetId,title,gridProperties))",
        )
        .execute()
    )


def sheet_map(svc) -> dict[str, int]:
    meta = _meta(svc)
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def ensure_sheet(svc, title: str, columns: int = len(HEADER)) -> int:
    titles = sheet_map(svc)
    if title in titles:
        return titles[title]
    resp = (
        svc.spreadsheets()
        .batchUpdate(
            spreadsheetId=settings.spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": title,
                                "gridProperties": {
                                    "rowCount": 2000,
                                    "columnCount": columns,
                                    "frozenRowCount": 1,
                                },
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )
    sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    log.info("создан лист %s id=%s", title, sid)
    return sid


def ensure_data_sheet(svc) -> int:
    return ensure_sheet(svc, settings.data_sheet)


def ensure_column_count(svc, sheet_id: int, n: int) -> None:
    """Расширяет сетку вправо. Уже более широкую не сужает."""
    meta = _meta(svc)
    current = 0
    for s in meta.get("sheets", []):
        props = s.get("properties") or {}
        if props.get("sheetId") == sheet_id:
            current = int((props.get("gridProperties") or {}).get("columnCount") or 0)
            break
    if current >= n:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=settings.spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"columnCount": n},
                        },
                        "fields": "gridProperties.columnCount",
                    }
                }
            ]
        },
    ).execute()


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


def write_rows(svc, title: str, rows: list[list[str]]) -> None:
    """Сначала header+N строк, потом clear хвоста. Не clear→update."""
    sid = ensure_sheet(svc, title)
    ensure_column_count(svc, sid, len(HEADER))
    n = len(rows)
    values = [list(HEADER)] + rows
    svc.spreadsheets().values().update(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A1",
        valueInputOption=settings.sheets_value_input,
        body={"values": values},
    ).execute()
    tail_from = n + 2
    svc.spreadsheets().values().clear(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A{tail_from}:{LAST_COL}",
        body={},
    ).execute()
    log.info("%s: записано %s строк, хвост с A%s:%s очищен", title, n, tail_from, LAST_COL)


def write_dannye(svc, rows: list[list[str]]) -> None:
    write_rows(svc, settings.data_sheet, rows)


def write_warehouse(svc, rows: list[list[str]]) -> None:
    """Лист «Склад»: на складе, но не в продаже. amo его не читает."""
    write_rows(svc, settings.warehouse_sheet, rows)


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
    A2 — FILTER. Имена/порядок только CORE_HEADER (A–O), amo не видит P+."""
    title = settings.sheet1_name
    svc.spreadsheets().values().clear(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A1:{LAST_CORE_COL}",
        body={},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=settings.spreadsheet_id,
        range=f"{qid(title)}!A1",
        valueInputOption="RAW",
        body={"values": [list(CORE_HEADER)]},
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
