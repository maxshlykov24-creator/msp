"""Чтение/запись Google Sheets."""
from __future__ import annotations

import time
from typing import Any, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GOOGLE_SA, SCOPES_SHEETS, SPREADSHEET_ID


def sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        str(GOOGLE_SA), scopes=SCOPES_SHEETS
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_values(svc, title: str, rng: str = "A:ZZ", render: str = "FORMATTED_VALUE") -> List[List[Any]]:
    resp = (
        svc.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{title}'!{rng}",
            valueRenderOption=render,
        )
        .execute()
    )
    return resp.get("values", [])


def get_values_retry(
    svc,
    title: str,
    rng: str = "A:ZZ",
    *,
    retries: int = 6,
    render: str = "FORMATTED_VALUE",
) -> List[List[Any]]:
    last: Optional[Exception] = None
    cur = svc
    for attempt in range(1, retries + 1):
        try:
            return get_values(cur, title, rng, render=render)
        except (HttpError, OSError, TimeoutError) as e:
            last = e
            print(f"  sheets retry {attempt}/{retries} '{title}': {e}", flush=True)
            time.sleep(min(2 ** attempt, 30))
            try:
                cur = sheets_service()
            except Exception:
                pass
    raise RuntimeError(f"sheets '{title}' failed after retries: {last}")


def _with_retry(fn, *, label: str, retries: int = 6):
    last: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except (HttpError, OSError, TimeoutError) as e:
            last = e
            print(f"  sheets retry {attempt}/{retries} {label}: {e}", flush=True)
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"sheets {label} failed after retries: {last}")


def ensure_sheet(svc, title: str, rows: int = 2000, cols: int = 24) -> int:
    def _get_meta():
        return (
            svc.spreadsheets()
            .get(spreadsheetId=SPREADSHEET_ID, fields="sheets(properties(sheetId,title))")
            .execute()
        )

    meta = _with_retry(_get_meta, label=f"ensure_sheet.get '{title}'")
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    requests = []
    if title in existing:
        requests.append({"deleteSheet": {"sheetId": existing[title]}})
    requests.append(
        {
            "addSheet": {
                "properties": {
                    "title": title,
                    "gridProperties": {"rowCount": rows, "columnCount": cols},
                }
            }
        }
    )
    resp = _with_retry(
        lambda: svc.spreadsheets()
        .batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests})
        .execute(),
        label=f"ensure_sheet.batch '{title}'",
    )
    for reply in resp.get("replies", []):
        if "addSheet" in reply:
            return reply["addSheet"]["properties"]["sheetId"]
    raise RuntimeError(f"Не удалось создать лист {title}")


def write_values(svc, title: str, values: List[List[Any]], sheet_id: Optional[int] = None):
    _with_retry(
        lambda: svc.spreadsheets()
        .values()
        .update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{title}'!A1",
            valueInputOption="RAW",
            body={"values": values},
        )
        .execute(),
        label=f"write_values '{title}'",
    )
    if sheet_id is not None and values:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {"bold": True},
                                    "backgroundColor": {
                                        "red": 0.90,
                                        "green": 0.93,
                                        "blue": 0.96,
                                    },
                                }
                            },
                            "fields": "userEnteredFormat(textFormat,backgroundColor)",
                        }
                    },
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                ]
            },
        ).execute()
