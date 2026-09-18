#!/usr/bin/env python3
"""Ответственный по графику смен. Только новые воронки. Таблица Оксаны 21.09.

Формат CSV: date,amo_user_id,name
По умолчанию сухой прогон. Не гоняет старую воронку.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import lib

TZ = ZoneInfo("Europe/Moscow")


def today_row(path: Path) -> dict | None:
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("date") or "").strip() == day:
                return row
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(Path(__file__).with_name("shifts.csv")))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.is_file():
        print(f"Нет {path}. Пример: shifts.example.csv. Оксана обещала таблицу 21.09.")
        return
    row = today_row(path)
    if not row:
        print("На сегодня в графике пусто")
        return
    uid = int(row["amo_user_id"])
    print(f"Смена {row.get('name')} id={uid}")
    amo = lib.Amo()
    for pipe in (lib.PIPELINE_SALES_NEW, lib.PIPELINE_MKT_NEW):
        leads = amo.iter_leads(f"?filter[pipeline_id]={pipe}")
        n = 0
        for lead in leads:
            if lead.get("status_id") in (142, 143):
                continue
            if lead.get("responsible_user_id") == uid:
                continue
            n += 1
            if args.apply:
                amo.req("PATCH", f"/api/v4/leads/{lead['id']}", {"responsible_user_id": uid})
        print(f"  воронка {pipe}: сменить ответственного {n}")


if __name__ == "__main__":
    main()
