"""Один раз после сверки: бэкап Sheet1 + FILTER. По умолчанию dry-run."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from app.config import settings
from app.sheets import (
    access_ok,
    duplicate_sheet,
    get_values,
    qid,
    set_sheet1_filter,
    sheets_service,
)

log = logging.getLogger("setup_sheet1")

# Русская локаль таблиц — точка с запятой. Если #ERROR, поменять на запятые.
FORMULA_RU = '=IFERROR(FILTER(Данные!A2:O; Данные!A2:A<>""); "")'
FORMULA_EN = '=IFERROR(FILTER(Данные!A2:O, Данные!A2:A<>""), "")'


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="реально переключить Sheet1")
    p.add_argument("--en-locale", action="store_true", help="разделитель формул запятая")
    args = p.parse_args()

    ok, info = access_ok()
    if not ok:
        print("нет доступа к таблице:", info, file=sys.stderr)
        print(
            "Выдай редактора на таблицу: cursor-sheets@lunar-caster-444612-p7.iam.gserviceaccount.com",
            file=sys.stderr,
        )
        return 2

    svc = sheets_service()
    data_rows = get_values(svc, settings.data_sheet, "A1:A")
    header_cell = (data_rows[0][0] if data_rows and data_rows[0] else "")
    filled = [r for r in data_rows[1:] if r and str(r[0]).strip()]
    header = get_values(svc, settings.sheet1_name, "A1:O1")
    formula = FORMULA_EN if args.en_locale else FORMULA_RU
    backup = f"Sheet1_backup_{date.today().isoformat()}"

    print("таблица:", info)
    print(f"Данные A1: {header_cell!r}")
    print(f"Данные строк: {len(filled)}")
    print(f"Sheet1 шапка сейчас: {header[0] if header else 'пусто'}")
    print("A1 Sheet1 сейчас формулы QUERY — при --apply заменятся статическим заголовком (имена A–O те же).")
    print(f"бэкап: {backup}")
    print(f"формула A2: {formula}")

    if header_cell != "VIN":
        print(
            "Данные ещё в старом формате (VIN не в колонке A). "
            "Сначала python -m app.sync, иначе виджет получит чужие колонки.",
            file=sys.stderr,
        )
        return 3

    if len(filled) == 0:
        print("Данные пусты — Sheet1 не переключаем.", file=sys.stderr)
        return 3

    if not args.apply:
        print("dry-run. Для переключения: python -m app.setup_sheet1 --apply")
        return 0

    duplicate_sheet(svc, settings.sheet1_name, backup)
    set_sheet1_filter(svc, formula)
    print(f"готово: {qid(settings.sheet1_name)}!A2 spill, откат = лист {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
