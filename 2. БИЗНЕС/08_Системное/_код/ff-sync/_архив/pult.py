"""Флажок на Пульте обрабатывает лист Вход, не весь каталог."""

from datetime import datetime

from clients_sync import ingest_new_names, refresh_client_gray
from db import init_db
from intake import run
from sheets import open_book, pult_done, pult_should_run, pult_start


def check_once():
    init_db()
    book = open_book()
    created = ingest_new_names(book)
    if created:
        refresh_client_gray(book)
        print("пульт: новые клиенты %s" % ", ".join(created))
    ws = book.worksheet("Пульт")
    if not pult_should_run(ws):
        return
    print("пульт: обрабатываю Вход")
    pult_start(ws)
    try:
        results = run()
        ok = sum(1 for x in results if x.startswith("создан") or x.startswith("обновлён"))
        err = sum(1 for x in results if x and not (x.startswith("создан") or x.startswith("обновлён")))
        text = "%s: обработано %s, ок %s, ошибок %s" % (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            len(results),
            ok,
            err,
        )
    except Exception as exc:
        text = "сбой: %s" % exc
        print(text)
    pult_done(ws, text)
    print("пульт:", text)
