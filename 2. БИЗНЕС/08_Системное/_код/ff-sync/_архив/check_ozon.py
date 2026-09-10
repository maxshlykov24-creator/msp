"""Проверка Ozon перед запуском: карточки, этикетка товара, этикетка отправления.

Только чтение и генерация файлов в /data. Этикетку отправления Ozon отдаёт
исключительно в статусе awaiting_deliver, поэтому проверяем на том, что есть.
"""

import os

from db import catalog_card, get_cabinet, init_db, list_assembly

OUT = os.environ.get("FF_CHECK_DIR", "/data")


def main():
    init_db()
    rows = [r for r in list_assembly(client_id=1, marketplace="ozon") if r["kind"] == "fbs"]
    print("Ozon FBS в выборке:", len(rows))
    by_status = {}
    for r in rows:
        by_status.setdefault(r["status"], 0)
        by_status[r["status"]] += 1
    print("по статусам:", by_status)
    ready = [r for r in rows if "отгруз" in (r["status"] or "")]
    print("ожидают отгрузки:", len(ready))
    pick = (ready or rows)[:3]
    import labels as labels_mod

    label_rows = []
    for s in pick:
        card = catalog_card(s["client_id"], barcode=s["barcode"] or "", article=s["article"] or "")
        print(
            "  %s арт=%s штрихкод отправления=%r из каталога=%r бренд=%r цвет=%r"
            % (
                s["ext_id"],
                s["article"],
                s["barcode"],
                card.get("barcode"),
                card.get("brand"),
                card.get("color"),
            )
        )
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
    if not label_rows:
        print("нет отправлений Ozon для проверки")
        return
    pdf, notes, pages = labels_mod.build(label_rows, mode=labels_mod.MODE_PRODUCT)
    with open(os.path.join(OUT, "check_ozon_product.pdf"), "wb") as fh:
        fh.write(pdf)
    print("этикетка товара Ozon: страниц", pages, "заметки", notes)
    if ready:
        try:
            pdf, notes, pages = labels_mod.build(label_rows, mode=labels_mod.MODE_POSTING)
            with open(os.path.join(OUT, "check_ozon_posting.pdf"), "wb") as fh:
                fh.write(pdf)
            print("этикетка отправления Ozon: страниц", pages, "заметки", notes)
        except Exception as exc:
            print("этикетка отправления Ozon не собралась:", exc)


if __name__ == "__main__":
    main()
