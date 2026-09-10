"""Проверка перед запуском: лист подбора, этикетки, «Куда везти» по живым данным.

Ничего не пишет в площадки и в МойСклад: только читает базу и собирает файлы
в /data, чтобы сверить их с личным кабинетом до необратимых шагов.
"""

import os
import sys

from db import init_db, list_assembly
from export_xlsx import build_picking

CLIENT = int(sys.argv[1]) if len(sys.argv) > 1 else 2
OUT = os.environ.get("FF_CHECK_DIR", "/data")


def main():
    init_db()
    rows = list_assembly(client_id=CLIENT)
    print("строк в выборке:", len(rows))
    new = [r for r in rows if str(r["work_state"] or "") in ("", "new")]
    print("из них не взято в сборку:", len(new))
    for r in rows[:8]:
        print(
            "  %s %s %s | арт %s | office=%r cargo=%r | %s"
            % (
                r["marketplace"],
                r["kind"],
                r["ext_id"],
                r["article"],
                r["office"] if "office" in r.keys() else None,
                r["cargo_type"] if "cargo_type" in r.keys() else None,
                r["status"],
            )
        )
    name, raw = build_picking(rows, who=rows[0]["client_name"] if rows else "")
    path = os.path.join(OUT, "check_picking.xlsx")
    with open(path, "wb") as fh:
        fh.write(raw)
    print("лист подбора:", name, len(raw), "байт →", path)

    import labels as labels_mod
    from db import catalog_card, get_cabinet

    pick = rows[:3]
    label_rows = []
    for s in pick:
        card = catalog_card(s["client_id"], barcode=s["barcode"] or "", article=s["article"] or "")
        label_rows.append(
            {
                "cabinet_id": s["cabinet_id"],
                "cabinet": get_cabinet(s["cabinet_id"]),
                "marketplace": s["marketplace"],
                "ext_id": s["ext_id"],
                "article": s["article"] or "",
                "barcode": s["barcode"] or card.get("barcode") or "",
                "name": s["name"] or card.get("name") or "",
                "client": s["client_name"] or "",
                "brand": card.get("brand") or "",
                "color": card.get("color") or "",
                "size": card.get("size") or "",
            }
        )
    if label_rows:
        pdf, notes, pages = labels_mod.build(label_rows, mode=labels_mod.MODE_PRODUCT)
        path = os.path.join(OUT, "check_labels.pdf")
        with open(path, "wb") as fh:
            fh.write(pdf)
        print("этикетки товара: страниц", pages, "заметки", notes, "→", path)


if __name__ == "__main__":
    main()
