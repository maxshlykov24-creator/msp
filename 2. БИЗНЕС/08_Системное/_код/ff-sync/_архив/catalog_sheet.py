"""Заливка CSV каталога в лист «Каталог». Отметки Привезено и Литраж_л при повторной заливке сохраняем."""

import csv
import os

from catalog_pull import FIELDS, out_dir
from sheets import ensure_sheets, open_book, set_checkbox, setup_pult


def is_on(val):
    return str(val or "").strip().upper() in ("TRUE", "ИСТИНА", "1", "ДА", "YES")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def existing_marks(ws):
    values = ws.get_all_values()
    if not values:
        return {}
    header = values[0]
    try:
        i_cab = header.index("cabinet_id")
        i_key = header.index("ext_key")
        i_mark = header.index("Привезено")
        i_lit = header.index("Литраж_л")
    except ValueError:
        return {}
    marks = {}
    for row in values[1:]:
        if len(row) <= max(i_cab, i_key):
            continue
        key = (row[i_cab], row[i_key])
        brought = row[i_mark] if len(row) > i_mark else ""
        liters = row[i_lit] if len(row) > i_lit else ""
        if is_on(brought) or liters:
            marks[key] = (is_on(brought), liters)
    return marks


def main():
    folder = out_dir()
    names = ("catalog_wb_1.csv", "catalog_ozon_2.csv")
    paths = []
    for name in names:
        for base in (folder, os.path.join(folder, "data")):
            cand = os.path.join(base, name)
            if os.path.exists(cand):
                paths.append(cand)
                break
        else:
            paths.append(os.path.join(folder, name))
    rows = []
    for path in paths:
        if not os.path.exists(path):
            print("нет файла %s" % path)
            continue
        chunk = read_csv(path)
        print("%s: %s строк" % (os.path.basename(path), len(chunk)))
        rows.extend(chunk)
    if not rows:
        raise SystemExit("нечего заливать")

    book = open_book()
    sheets = ensure_sheets(book)
    ws = sheets["Каталог"]
    marks = existing_marks(ws)
    kept = 0
    for row in rows:
        key = (row.get("cabinet_id") or "", row.get("ext_key") or "")
        if key in marks:
            on, liters = marks[key]
            row["Привезено"] = bool(on)
            row["Литраж_л"] = liters
            kept += 1
        else:
            row["Привезено"] = False
    values = [list(FIELDS)]
    for row in rows:
        line = []
        for col in FIELDS:
            val = row.get(col, "")
            if col == "Привезено":
                val = bool(val) if not isinstance(val, bool) else val
            line.append(val)
        values.append(line)
    ws.clear()
    ws.update(values, "A1", value_input_option="USER_ENTERED")
    ws.freeze(rows=1)
    set_checkbox(ws, 1, len(values), FIELDS.index("Привезено"))
    setup_pult(book)
    print("лист Каталог: записано %s строк, сохранено отметок %s, колонка Привезено — флажки" % (len(rows), kept))
    print("таблица: %s" % book.url)


if __name__ == "__main__":
    main()
