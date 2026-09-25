"""Остаток склада Фулфилмент на FBS-склады WB, Ozon и Яндекса.

На площадку уходит только то, что сопоставилось по штрихкоду у одного контрагента.
Чужие карточки не обнуляем. Пустой ответ МойСклад не считаем нулём.
Полный проход и таймер включаются отдельно, после проверки трёх позиций.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from db import barcode_norm, connect, get_client, init_db, list_cabinets, update_cabinet
from net import MS_BASE, OZON_BASE, YANDEX_BASE, ms_headers, ozon_headers, req, wb_headers, yandex_headers

MSK = timezone(timedelta(hours=3))


class Stop(Exception):
    """Запись не начата: условие плана не выполнено."""


def physical(stock, reserve):
    try:
        qty = float(stock or 0) + float(reserve or 0)
    except (TypeError, ValueError):
        return None
    if qty < 0:
        qty = 0
    return int(qty)


def ozon_fbs_warehouses(warehouses):
    """Склад, который принимает остаток: создан и это не rFBS. Выключенные не считаем."""
    live = []
    for row in warehouses or []:
        if (row.get("status") or "").lower() != "created":
            continue
        if row.get("is_rfbs"):
            continue
        live.append(row)
    return live


def yandex_fbs_campaigns(campaigns):
    """Только FBS с живым API. FBY и выключенные кампании не берём."""
    live = []
    for row in campaigns or []:
        if row.get("placementType") != "FBS":
            continue
        if row.get("apiAvailability") != "AVAILABLE":
            continue
        live.append(row)
    return live


def one_warehouse(rows, label):
    if len(rows) != 1:
        names = [str(r.get("name") or r.get("domain") or r.get("id")) for r in rows]
        raise Stop("%s: нужен один склад FBS, сейчас %s (%s)" % (label, len(rows), ", ".join(names) or "пусто"))
    return rows[0]


def product_id(row):
    href = (((row.get("assortment") or {}).get("meta") or {}).get("href")) or ""
    return href.rstrip("/").split("/")[-1]


def ms_rows(store_id):
    href = MS_BASE + "/entity/store/" + store_id
    rows = []
    offset = 0
    while True:
        r = req(
            "GET",
            MS_BASE + "/report/stock/all",
            headers=ms_headers(),
            params={"limit": 1000, "offset": offset, "filter": "store=%s" % href},
        )
        if r.status_code != 200:
            raise Stop("остаток МойСклад %s %s" % (r.status_code, (r.text or "")[:180]))
        data = r.json()
        if "rows" not in data:
            raise Stop("остаток МойСклад пришёл без списка")
        batch = data["rows"] or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def client_cards(agent_id, attr_id):
    """Товары, у которых «Клиент фулфилмента» указывает на этого контрагента."""
    if not attr_id or not agent_id:
        raise Stop("нет поля «Клиент фулфилмента» или контрагента")
    filt = "%s/entity/product/metadata/attributes/%s=%s/entity/counterparty/%s" % (
        MS_BASE,
        attr_id,
        MS_BASE,
        agent_id,
    )
    cards = {}
    offset = 0
    while True:
        r = req(
            "GET",
            MS_BASE + "/entity/product",
            headers=ms_headers(),
            params={"limit": 100, "offset": offset, "filter": filt},
        )
        if r.status_code != 200:
            raise Stop("товары контрагента %s %s" % (r.status_code, (r.text or "")[:180]))
        data = r.json()
        if "rows" not in data:
            raise Stop("товары контрагента пришли без списка")
        batch = data["rows"] or []
        for row in batch:
            codes = []
            for bc in row.get("barcodes") or []:
                if isinstance(bc, dict):
                    codes.extend(str(v) for v in bc.values() if v)
                elif bc:
                    codes.append(str(bc))
            cards[row.get("id")] = {
                "article": row.get("article") or "",
                "name": row.get("name") or "",
                "barcodes": codes,
            }
        if len(batch) < 100:
            break
        offset += 100
    return cards


def index_cabinet(cabinet_id):
    """Штрихкод → одна карточка. Две карточки на один штрихкод — спор, его не шлём."""
    conn = connect()
    rows = conn.execute(
        "SELECT ext_key, ext_article, ext_barcode, barcode_norm, name FROM catalog_cache WHERE cabinet_id = ?",
        (cabinet_id,),
    ).fetchall()
    conn.close()
    found = {}
    clash = set()
    for row in rows:
        key = row["barcode_norm"] or ""
        if not key:
            continue
        item = {
            "ext_key": row["ext_key"],
            "article": row["ext_article"] or "",
            "barcode": row["ext_barcode"] or "",
            "name": row["name"] or "",
        }
        prev = found.get(key)
        if prev and prev["ext_key"] != item["ext_key"]:
            clash.add(key)
            continue
        found[key] = item
    for key in clash:
        found.pop(key, None)
    return found, clash


def trios(cards, stock_by_product, wb_idx, oz_idx, ya_idx):
    """Один штрихкод сразу на трёх площадках. Размер, не артикул."""
    out = []
    seen = set()
    for pid, card in cards.items():
        qty = stock_by_product.get(pid)
        if qty is None or qty <= 0:
            continue
        for raw in card["barcodes"]:
            code = barcode_norm(raw)
            if not code or code in seen:
                continue
            wb = wb_idx.get(code)
            oz = oz_idx.get(code)
            ya = ya_idx.get(code)
            if not (wb and oz and ya):
                continue
            seen.add(code)
            out.append(
                {
                    "product_id": pid,
                    "article": card["article"],
                    "name": card["name"],
                    "barcode": code,
                    "qty": qty,
                    "wb": wb,
                    "ozon": oz,
                    "yandex": ya,
                }
            )
    out.sort(key=lambda item: item["barcode"])
    return out


def cabinets_of(client_id):
    found = {}
    for row in list_cabinets():
        if row["client_id"] != client_id or not row["active"] or not row["token"]:
            continue
        found.setdefault(row["marketplace"], row)
    return found


def require_three(cabs):
    missing = [name for name in ("wb", "ozon", "yandex") if name not in cabs]
    if missing:
        raise Stop("у контрагента нет кабинетов: %s" % ", ".join(missing))
    return cabs["wb"], cabs["ozon"], cabs["yandex"]


def discover_wb(token):
    r = req("GET", "https://marketplace-api.wildberries.ru/api/v3/warehouses", headers=wb_headers(token))
    if r.status_code != 200:
        raise Stop("склады WB %s %s" % (r.status_code, (r.text or "")[:180]))
    rows = r.json()
    if not isinstance(rows, list):
        raise Stop("склады WB пришли без списка")
    return one_warehouse(rows, "WB")


def discover_ozon(client_id_ext, token):
    r = req(
        "POST",
        OZON_BASE + "/v2/warehouse/list",
        headers=ozon_headers(client_id_ext, token),
        json={"limit": 50},
    )
    if r.status_code != 200:
        raise Stop("склады Ozon %s %s" % (r.status_code, (r.text or "")[:180]))
    data = r.json()
    if "warehouses" not in data:
        raise Stop("склады Ozon пришли без списка")
    return one_warehouse(ozon_fbs_warehouses(data["warehouses"]), "Ozon")


def discover_yandex(token):
    r = req("GET", YANDEX_BASE + "/v2/campaigns", headers=yandex_headers(token))
    if r.status_code != 200:
        raise Stop("кампании Яндекса %s %s" % (r.status_code, (r.text or "")[:180]))
    data = r.json()
    if "campaigns" not in data:
        raise Stop("кампании Яндекса пришли без списка")
    return one_warehouse(yandex_fbs_campaigns(data["campaigns"]), "Яндекс")


def read_wb(token, warehouse_id, chrt_ids):
    r = req(
        "POST",
        "https://marketplace-api.wildberries.ru/api/v3/stocks/%s" % warehouse_id,
        headers=wb_headers(token),
        json={"chrtIds": [int(x) for x in chrt_ids]},
    )
    if r.status_code != 200:
        raise Stop("чтение остатка WB %s %s" % (r.status_code, (r.text or "")[:180]))
    out = {}
    for row in (r.json() or {}).get("stocks") or []:
        out[str(row.get("chrtId"))] = row.get("amount")
    return out


def read_ozon(client_id_ext, token, warehouse_id, offer_ids):
    r = req(
        "POST",
        OZON_BASE + "/v2/product/info/stocks-by-warehouse/fbs",
        headers=ozon_headers(client_id_ext, token),
        json={"offer_id": list(offer_ids), "limit": 100},
    )
    if r.status_code != 200:
        raise Stop("чтение остатка Ozon %s %s" % (r.status_code, (r.text or "")[:180]))
    out = {}
    for row in (r.json().get("products") or r.json().get("result") or []):
        if str(row.get("warehouse_id")) != str(warehouse_id):
            continue
        out[row.get("offer_id")] = row.get("present")
    return out


def read_yandex(token, campaign_id, skus):
    r = req(
        "POST",
        YANDEX_BASE + "/v2/campaigns/%s/offers/stocks" % campaign_id,
        headers=yandex_headers(token),
        json={"offerIds": list(skus)},
    )
    if r.status_code != 200:
        raise Stop("чтение остатка Яндекса %s %s" % (r.status_code, (r.text or "")[:180]))
    out = {}
    result = (r.json().get("result") or {})
    for row in result.get("warehouses") or result.get("skus") or []:
        sku = row.get("offerId") or row.get("sku")
        items = row.get("stocks") or row.get("items") or []
        if items:
            out[sku] = items[0].get("count")
    return out


def send_wb(token, warehouse_id, items):
    r = req(
        "PUT",
        "https://marketplace-api.wildberries.ru/api/v3/stocks/%s" % warehouse_id,
        headers=wb_headers(token),
        json={"stocks": [{"chrtId": int(item["chrt"]), "amount": int(item["qty"])} for item in items]},
    )
    if r.status_code not in (200, 204):
        raise Stop("запись WB %s %s" % (r.status_code, (r.text or "")[:180]))


def send_ozon(client_id_ext, token, warehouse_id, items):
    r = req(
        "POST",
        OZON_BASE + "/v2/products/stocks",
        headers=ozon_headers(client_id_ext, token),
        json={
            "stocks": [
                {"offer_id": item["offer"], "stock": int(item["qty"]), "warehouse_id": int(warehouse_id)}
                for item in items
            ]
        },
    )
    if r.status_code != 200:
        raise Stop("запись Ozon %s %s" % (r.status_code, (r.text or "")[:180]))
    bad = []
    for row in r.json().get("result") or []:
        if not row.get("updated"):
            bad.append("%s: %s" % (row.get("offer_id"), row.get("errors")))
    if bad:
        raise Stop("Ozon не принял: %s" % "; ".join(bad)[:300])


def send_yandex(token, campaign_id, items):
    now = datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00")
    r = req(
        "PUT",
        YANDEX_BASE + "/v2/campaigns/%s/offers/stocks" % campaign_id,
        headers=yandex_headers(token),
        json={
            "skus": [
                {"sku": item["sku"], "items": [{"count": int(item["qty"]), "updatedAt": now}]}
                for item in items
            ]
        },
    )
    if r.status_code != 200:
        raise Stop("запись Яндекса %s %s" % (r.status_code, (r.text or "")[:180]))
    if r.json().get("status") not in (None, "OK"):
        raise Stop("запись Яндекса %s" % ((r.text or "")[:180]))


def build(client_code, limit=3):
    init_db()
    client = get_client(client_code)
    if not client:
        raise Stop("контрагент %s не найден" % client_code)
    store = client["ms_store_id"] or os.environ.get("MS_STORE_ID") or ""
    if not store:
        raise Stop("нет склада Фулфилмент")
    from db import get_setting

    cards = client_cards(client["ms_counterparty_id"], get_setting("ATTR_CLIENT_ID"))
    stock_rows = ms_rows(store)
    stock_by_product = {}
    for row in stock_rows:
        pid = product_id(row)
        if pid not in cards:
            continue
        qty = physical(row.get("stock"), row.get("reserve"))
        if qty is None:
            raise Stop("в остатке МойСклад нечисловое количество")
        stock_by_product[pid] = qty
    wb, oz, ya = require_three(cabinets_of(client["id"]))
    wb_wh = discover_wb(wb["token"])
    oz_wh = discover_ozon(oz["client_id_ext"], oz["token"])
    ya_camp = discover_yandex(ya["token"])
    wb_idx, wb_clash = index_cabinet(wb["id"])
    oz_idx, oz_clash = index_cabinet(oz["id"])
    ya_idx, ya_clash = index_cabinet(ya["id"])
    found = trios(cards, stock_by_product, wb_idx, oz_idx, ya_idx)
    return {
        "client": client,
        "wb": wb,
        "ozon": oz,
        "yandex": ya,
        "wb_warehouse": wb_wh,
        "ozon_warehouse": oz_wh,
        "yandex_campaign": ya_camp,
        "clash": len(wb_clash) + len(oz_clash) + len(ya_clash),
        "matched": len(found),
        "pick": found[:limit],
    }


def push_three(client_code):
    """Пишет остаток трёх позиций. Больше этого захода не отправляет."""
    plan = build(client_code, limit=3)
    if plan["matched"] < 3:
        raise Stop("на трёх площадках сразу нашлось %s позиций, нужно 3. Запись не начата" % plan["matched"])
    pick = plan["pick"]
    wb, oz, ya = plan["wb"], plan["ozon"], plan["yandex"]
    wb_id = plan["wb_warehouse"]["id"]
    oz_id = plan["ozon_warehouse"]["warehouse_id"]
    camp = plan["yandex_campaign"]["id"]
    before = {
        "wb": read_wb(wb["token"], wb_id, [item["wb"]["ext_key"] for item in pick]),
        "ozon": read_ozon(oz["client_id_ext"], oz["token"], oz_id, [item["ozon"]["ext_key"] for item in pick]),
        "yandex": read_yandex(ya["token"], camp, [item["yandex"]["ext_key"] for item in pick]),
    }
    send_wb(wb["token"], wb_id, [{"chrt": item["wb"]["ext_key"], "qty": item["qty"]} for item in pick])
    send_ozon(
        oz["client_id_ext"],
        oz["token"],
        oz_id,
        [{"offer": item["ozon"]["ext_key"], "qty": item["qty"]} for item in pick],
    )
    send_yandex(ya["token"], camp, [{"sku": item["yandex"]["ext_key"], "qty": item["qty"]} for item in pick])
    after = {
        "wb": read_wb(wb["token"], wb_id, [item["wb"]["ext_key"] for item in pick]),
        "ozon": read_ozon(oz["client_id_ext"], oz["token"], oz_id, [item["ozon"]["ext_key"] for item in pick]),
        "yandex": read_yandex(ya["token"], camp, [item["yandex"]["ext_key"] for item in pick]),
    }
    update_cabinet(wb["id"], stock_warehouse_id=str(wb_id))
    update_cabinet(oz["id"], stock_warehouse_id=str(oz_id))
    update_cabinet(ya["id"], stock_warehouse_id=str(camp))
    report = []
    for item in pick:
        chrt = str(item["wb"]["ext_key"])
        offer = item["ozon"]["ext_key"]
        sku = item["yandex"]["ext_key"]
        report.append(
            {
                "article": item["article"],
                "barcode": item["barcode"],
                "name": item["name"],
                "qty": item["qty"],
                "before": {
                    "wb": before["wb"].get(chrt),
                    "ozon": before["ozon"].get(offer),
                    "yandex": before["yandex"].get(sku),
                },
                "after": {
                    "wb": after["wb"].get(chrt),
                    "ozon": after["ozon"].get(offer),
                    "yandex": after["yandex"].get(sku),
                },
            }
        )
    folder = os.environ.get("FF_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if folder.endswith(".db"):
        folder = os.path.dirname(folder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "stock_push_prev.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"saved": before, "pick": report}, fh, ensure_ascii=False, indent=2)
    return report


def _self_check():
    assert physical(2, 1) == 3
    assert physical(0, 0) == 0
    assert physical("x", 1) is None
    oz = ozon_fbs_warehouses(
        [
            {"name": "старый", "status": "disabled", "is_rfbs": False},
            {"name": "rfbs", "status": "created", "is_rfbs": True},
            {"name": "Домодедовская 28", "status": "created", "is_rfbs": False, "warehouse_id": 1},
        ]
    )
    assert len(oz) == 1 and oz[0]["name"] == "Домодедовская 28"
    ya = yandex_fbs_campaigns(
        [
            {"domain": "GripOn", "placementType": "FBS", "apiAvailability": "DISABLED_BY_INACTIVITY"},
            {"domain": "2", "placementType": "FBS", "apiAvailability": "DISABLED_BY_INACTIVITY"},
            {"domain": "Grip On", "placementType": "FBY", "apiAvailability": "AVAILABLE"},
        ]
    )
    assert ya == []
    try:
        one_warehouse(ya, "Яндекс")
    except Stop as exc:
        assert "Яндекс" in str(exc)
    else:
        raise SystemExit("пустой Яндекс должен останавливать")
    print("stock_push: проверки прошли")


if __name__ == "__main__":
    _self_check()
