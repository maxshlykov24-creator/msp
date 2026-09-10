"""Проверка хостов. Без кабинетов в базе: МойСклад с токеном, WB /ping без токена (ожидаем 401), Ozon без ключей не дергаем."""

from db import init_db, list_cabinets
from net import MS_BASE, OZON_BASE, WB_BASE, ms_headers, ozon_headers, req, wb_headers


def ping_ms():
    r = req("GET", MS_BASE + "/entity/organization?limit=1", headers=ms_headers())
    body = (r.text or "")[:180].replace("\n", " ")
    print("МойСклад  %s  %s" % (r.status_code, body))
    return r.status_code


def ping_wb(token=None):
    headers = wb_headers(token) if token else {}
    r = req("GET", WB_BASE + "/ping", headers=headers)
    body = (r.text or "")[:180].replace("\n", " ")
    print("WB        %s  %s" % (r.status_code, body))
    return r.status_code


def ping_ozon(client_id, api_key):
    r = req(
        "POST",
        OZON_BASE + "/v3/product/list",
        headers=ozon_headers(client_id, api_key),
        json={"filter": {"visibility": "ALL"}, "limit": 1},
    )
    body = (r.text or "")[:180].replace("\n", " ")
    print("Ozon      %s  %s" % (r.status_code, body))
    return r.status_code


def main():
    init_db()
    ping_ms()
    cabs = list_cabinets()
    wb_done = False
    ozon_done = False
    for cab in cabs:
        if cab["marketplace"] == "wb" and cab["token"]:
            print("кабинет %s %s" % (cab["id"], cab["name"] or "wb"))
            ping_wb(cab["token"])
            wb_done = True
        elif cab["marketplace"] == "ozon" and cab["token"] and cab["client_id_ext"]:
            print("кабинет %s %s" % (cab["id"], cab["name"] or "ozon"))
            ping_ozon(cab["client_id_ext"], cab["token"])
            ozon_done = True
    if not wb_done:
        print("WB без токена (ожидаем 401)")
        ping_wb(None)
    if not ozon_done:
        print("Ozon: кабинета в базе нет, запрос пропускаю")


if __name__ == "__main__":
    main()
