"""Заливает ведомость доступов кассы в готовую гугл-таблицу.

Сервисный аккаунт своего места на Диске не имеет и создать файл не может, поэтому
пустую таблицу заводит владелец и даёт доступ редактора сервисному аккаунту
(client_email из _private/google_sheets_sa.json). Сюда передаётся её id.

Пароли лежат в _private/ведомость_доступов_кассы.csv и в git не попадают.

Запуск: python3 scripts/make_access_sheet.py <spreadsheet_id>
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parents[1]
SA = ROOT / "_private" / "google_sheets_sa.json"
SOURCE = ROOT / "_private" / "ведомость_доступов_кассы.csv"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_TITLE = "Доступы"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Укажи id таблицы: python3 scripts/make_access_sheet.py <spreadsheet_id>")
    sid = sys.argv[1]

    with SOURCE.open(encoding="utf-8") as fh:
        values = [row for row in csv.reader(fh)]

    creds = service_account.Credentials.from_service_account_file(str(SA), scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    meta = svc.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties.title").execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if SHEET_TITLE not in titles:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TITLE}}}]},
        ).execute()

    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{SHEET_TITLE}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    print(f"Готово: https://docs.google.com/spreadsheets/d/{sid}/edit")


if __name__ == "__main__":
    main()
