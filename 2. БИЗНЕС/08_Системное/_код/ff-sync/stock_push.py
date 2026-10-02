"""Остатки МойСклад на FBS-склады WB, Ozon и Яндекса.

run_own: свои юрлица, Основной склад, независимое сопоставление кабинетов.
Старые build/push_three: прежний ограниченный проход фулфилмента.

На площадку уходит только то, что сопоставилось по штрихкоду у одного контрагента.
Чужие карточки не обнуляем. Пустой ответ МойСклад не считаем нулём.
Полный проход и таймер включаются отдельно, после проверки трёх позиций.
"""

import json
import os
import math
import time
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

from db import barcode_norm, connect, get_client, init_db, list_cabinets, update_cabinet
from net import MS_BASE, OZON_BASE, ms_headers, ozon_headers, req, wb_headers

YANDEX_BASE = "https://api.partner.market.yandex.ru"


def yandex_headers(token):
    return {"Api-Key": token, "Content-Type": "application/json"}

MSK = timezone(timedelta(hours=3))


class Stop(Exception):
    """Запись не начата: условие плана не выполнено."""


def physical(stock, reserve):
    try:
        qty = float(stock) - float(reserve)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(qty):
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
    href = (row.get("meta") or {}).get("href") or (((row.get("assortment") or {}).get("meta") or {}).get("href")) or ""
    return urlsplit(href).path.rstrip("/").split("/")[-1]


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
        if not isinstance(data.get("rows"), list):
            raise Stop("остаток МойСклад пришёл без списка")
        batch = data["rows"] or []
        rows.extend(batch)
        if len(batch) < 1000:
            if (data.get("meta") or {}).get("size", len(rows)) > len(rows):
                raise Stop("неполный отчёт остатков МойСклад")
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
    return one_warehouse([w for w in rows if w.get("deliveryType") == 1 and not w.get("isDeleting") and not w.get("isProcessing")], "WB")


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
    if not isinstance(r.json().get("stocks"), list):
        raise Stop("WB не вернул список остатков")
    out = {}
    for row in (r.json() or {}).get("stocks") or []:
        out[str(row.get("chrtId"))] = row.get("amount")
    return out


def read_ozon(client_id_ext, token, warehouse_id, offer_ids):
    out, cursor, seen = {}, "", set()
    while True:
        body = {"offer_id": list(offer_ids), "limit": 100}
        if cursor:
            body["cursor"] = cursor
        r = req("POST", OZON_BASE + "/v2/product/info/stocks-by-warehouse/fbs",
                headers=ozon_headers(client_id_ext, token), json=body)
        if r.status_code != 200:
            raise Stop("чтение остатка Ozon HTTP %s" % r.status_code)
        data = r.json()
        if not isinstance(data.get("products"), list):
            raise Stop("Ozon: нет списка остатков")
        for row in data["products"]:
            if str(row.get("warehouse_id")) != str(warehouse_id):
                continue
            # Запись задаёт свободный остаток, present включает резерв Ozon.
            qty = row.get("free_stock")
            if qty is None:
                qty = physical(row.get("present"), row.get("reserved"))
            out[str(row.get("offer_id"))] = qty
        if not data.get("has_next"):
            return out
        cursor = data.get("cursor")
        if not cursor or cursor in seen:
            raise Stop("Ozon: неполная пагинация остатков")
        seen.add(cursor)


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
    warehouses = result.get("warehouses") or []
    if len(warehouses) != 1:
        raise Stop("Яндекс: неоднозначный склад при чтении")
    for row in warehouses[0].get("offers") or []:
        stock = [x for x in row.get("stocks", []) if x.get("type") == "AVAILABLE"]
        if len(stock) == 1:
            out[str(row.get("offerId"))] = stock[0].get("count")
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
    result = r.json().get("result") or []
    if {str(x.get("offer_id")) for x in result} != {str(x["offer"]) for x in items}:
        raise Stop("Ozon вернул неполное подтверждение записи")
    bad = []
    for row in result:
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


# Свои кабинеты: отдельная конфигурация, без регистрации в очереди фулфилмента.
# В конфигурации нет автоматического включения: нужен успешный пилот.
OWN_STORE = "bad1b6d6-4f99-11f1-0a80-07550012fedb"
OWN_SUPPLIER = "6fa1608b-bd6e-11f1-0a80-1c880022c69e"


def own_path(name="own_stock.json"):
    from db import db_path
    return os.path.join(os.path.dirname(db_path()), name)


def save_private(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def own_config():
    with open(own_path(), encoding="utf-8") as fh:
        config = json.load(fh)
    if config.get("store_id") != OWN_STORE or config.get("supplier_id") != OWN_SUPPLIER:
        raise Stop("неподтверждённый источник своих остатков")
    ids = [t["id"] for t in config["targets"]]
    if len(ids) != len(set(ids)):
        raise Stop("повтор кабинета в настройках")
    for target in config["targets"]:
        if target.get("entity") not in ("1000", "1001", "1002", "1003"):
            raise Stop("кабинет вне четырёх своих юрлиц")
        if target.get("cabinet_id"):
            from db import get_cabinet
            cab = get_cabinet(target["cabinet_id"])
            if not cab or cab["marketplace"] != target["marketplace"]:
                raise Stop("неверная ссылка на кабинет")
            target["token"] = cab["token"]
    return config


def own_cards():
    cards = {}
    offset = 0
    while True:
        r = req("GET", MS_BASE + "/entity/product", headers=ms_headers(), params={
            "limit": 100, "offset": offset,
            "filter": "supplier=" + MS_BASE + "/entity/counterparty/" + OWN_SUPPLIER,
        })
        if r.status_code != 200 or not isinstance(r.json().get("rows"), list):
            raise Stop("не удалось полностью прочитать свою номенклатуру: HTTP %s" % r.status_code)
        batch = r.json()["rows"]
        for row in batch:
            if row.get("archived") or any(a.get("name") == "Клиент фулфилмента" and a.get("value") for a in row.get("attributes", [])):
                continue
            codes = [str(v) for bc in row.get("barcodes", []) for v in bc.values() if v]
            cards[row["id"]] = {"article": row.get("article", ""), "name": row.get("name", ""), "barcodes": codes}
        if len(batch) < 100:
            break
        offset += 100
    if not cards:
        raise Stop("своя номенклатура пуста; запись запрещена")
    return cards


def own_source():
    cards = own_cards()
    rows = ms_rows(OWN_STORE)
    if not rows:
        raise Stop("пустой отчёт МойСклад; массовое обнуление запрещено")
    quantities = {}
    for row in rows:
        pid = product_id(row)
        if not pid or pid in quantities:
            raise Stop("неоднозначная строка отчёта МойСклад")
        qty = physical(row.get("stock"), row.get("reserve"))
        if qty is None:
            raise Stop("нечисловой остаток или резерв МойСклад")
        quantities[pid] = qty
    if not set(cards).intersection(quantities):
        raise Stop("в отчёте нет своих товаров; запись запрещена")
    # Полный непустой отчёт получен: отсутствующие в нём товары каталога имеют ноль.
    return cards, {pid: quantities.get(pid, 0) for pid in cards}


def match_own(cards, quantities, rows):
    """Каждый оффер отдельно. Любая неоднозначность МС/кабинета исключается."""
    codes = {}
    for pid, card in cards.items():
        for raw in card["barcodes"]:
            code = str(raw or "").strip()
            if code and not code.upper().startswith("OZN"):
                codes.setdefault(code, set()).add(pid)
    offers = {}
    barcode_offers = {}
    for row in rows:
        ext = str(row.get("ext_key") or "")
        code = str(row.get("ext_barcode") or "").strip()
        if not ext or not code:
            continue
        offers.setdefault(ext, set()).add(code)
        barcode_offers.setdefault(code, set()).add(ext)
    matched, conflicts, unmatched = [], [], []
    for ext, barcodes in offers.items():
        pids = set().union(*(codes.get(c, set()) for c in barcodes))
        ambiguous = any(len(codes.get(c, set())) > 1 or len(barcode_offers[c]) > 1 for c in barcodes if c in codes)
        if ambiguous or len(pids) > 1:
            conflicts.append(ext)
        elif len(pids) == 1:
            pid = next(iter(pids))
            matched.append({"ext_key": ext, "product_id": pid, "qty": quantities[pid],
                            "article": cards[pid]["article"], "name": cards[pid]["name"],
                            "barcodes": sorted(barcodes.intersection(codes))})
        else:
            unmatched.append(ext)
    matched.sort(key=lambda x: x["ext_key"])
    return matched, conflicts, unmatched


def own_warehouse(target):
    mp = target["marketplace"]
    if mp == "wb":
        warehouse = discover_wb(target["token"])
        wid = warehouse["id"]
    elif mp == "ozon":
        r = req("POST", OZON_BASE + "/v2/warehouse/list", headers=ozon_headers(target["client_id_ext"], target["token"]), json={"limit": 50})
        if r.status_code != 200:
            raise Stop("склады Ozon HTTP %s" % r.status_code)
        data = r.json()
        if data.get("has_next"):
            raise Stop("список складов Ozon неполон")
        warehouses = ozon_fbs_warehouses(data.get("warehouses"))
        if target.get("warehouse_id"):
            warehouses = [w for w in warehouses if str(w["warehouse_id"]) == str(target["warehouse_id"])]
        warehouse = one_warehouse(warehouses, "Ozon")
        wid = warehouse["warehouse_id"]
    elif mp == "yandex":
        warehouse = discover_yandex(target["token"])
        wid = warehouse["id"]
    else:
        raise Stop("неизвестная площадка")
    if target.get("warehouse_id") and str(target["warehouse_id"]) != str(wid):
        raise Stop("склад кабинета изменился")
    return wid


def own_catalog(target, warehouse):
    from catalog_pull import pull_wb, rows_wb, pull_ozon, ozon_info, rows_ozon
    path = own_path("own_stock_catalog_" + target["id"] + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3600:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    mp = target["marketplace"]
    if mp == "wb":
        rows = rows_wb(0, pull_wb(target["token"]))
    elif mp == "ozon":
        items = pull_ozon(target["client_id_ext"], target["token"])
        offers = [i["offer_id"] for i in items]
        info = ozon_info(target["client_id_ext"], target["token"], offers)
        if set(offers) != set(info):
            raise Stop("неполный каталог Ozon")
        rows = rows_ozon(0, items, info)
    else:
        from catalog_pull import pull_yandex, rows_yandex
        rows = rows_yandex(0, pull_yandex(target["token"], warehouse))
    if not rows:
        raise Stop("каталог кабинета пуст")
    save_private(path, rows)
    return rows


def own_read(target, warehouse, items):
    keys = [x["ext_key"] for x in items]
    if target["marketplace"] == "wb":
        return read_wb(target["token"], warehouse, keys)
    if target["marketplace"] == "ozon":
        return read_ozon(target["client_id_ext"], target["token"], warehouse, keys)
    return read_yandex(target["token"], warehouse, keys)


def own_send(target, warehouse, items):
    if target["marketplace"] == "wb":
        return send_wb(target["token"], warehouse, [{"chrt": x["ext_key"], "qty": x["qty"]} for x in items])
    if target["marketplace"] == "ozon":
        return send_ozon(target["client_id_ext"], target["token"], warehouse, [{"offer": x["ext_key"], "qty": x["qty"]} for x in items])
    return send_yandex(target["token"], warehouse, [{"sku": x["ext_key"], "qty": x["qty"]} for x in items])


def run_own(mode="preview", target_id=None, pilot_product_ids=None):
    """preview / pilot (до трёх офферов) / sync. Снимок пишется ДО записи."""
    from db import run_lock
    if mode not in ("preview", "pilot", "sync"):
        raise ValueError(mode)
    with run_lock("own-stock", blocking=False):
        config = own_config()
        if mode == "pilot" and not target_id:
            raise Stop("пилот запускается только для одного конкретного кабинета")
        if mode == "sync" and config.get("shared_stock_confirmed") is not True:
            raise Stop("нужно решение об общем остатке между своими юрлицами")
        if mode == "sync" and not config.get("enabled"):
            return {"status": "disabled"}
        cards, quantities = own_source()
        journal = own_path("own_stock_run_" + datetime.now(MSK).strftime("%Y%m%dT%H%M%S%f") + ".json") if mode != "preview" else own_path("own_stock_preview.json")
        report = {"at": datetime.now(MSK).isoformat(), "mode": mode, "source_products": len(cards), "targets": []}
        for target in config["targets"]:
            if target_id and target["id"] != target_id:
                continue
            result = {"id": target["id"], "status": "pending"}
            report["targets"].append(result)
            try:
                warehouse = own_warehouse(target)
                rows = own_catalog(target, warehouse)
                if mode != "preview":
                    cards, quantities = own_source()
                items, conflicts, unmatched = match_own(cards, quantities, rows)
                result.update(warehouse_id=warehouse, matched=len(items), conflicts=conflicts, unmatched=unmatched,
                              positive=sum(x["qty"] > 0 for x in items), zero=sum(x["qty"] == 0 for x in items))
                if not items:
                    raise Stop("нет однозначных совпадений")
                result["sample"] = items[:3]
                if mode == "preview":
                    result["status"] = "ready"
                    continue
                pilot_path = own_path("own_stock_pilot_" + target["id"] + ".json")
                if mode == "sync":
                    if not os.path.exists(pilot_path):
                        raise Stop("нет проверенного пилота")
                    with open(pilot_path, encoding="utf-8") as fh:
                        pilot = json.load(fh)
                    if pilot.get("status") != "verified" or str(pilot.get("warehouse_id")) != str(warehouse):
                        raise Stop("пилот для этого склада не подтверждён")
                else:
                    # Пилот: положительные остатки, плюс ноль при наличии.
                    all_items = items
                    readable = {}
                    for start in range(0, len(items), 100):
                        readable.update(own_read(target, warehouse, items[start:start + 100]))
                    positive = [x for x in items if x["qty"] > 0 and x["ext_key"] in readable]
                    zero = [x for x in items if x["qty"] == 0 and x["ext_key"] in readable]
                    items = (positive[:2] + zero[:1] + positive[2:3])[:3]
                    if pilot_product_ids is not None:
                        chosen = set(pilot_product_ids)
                        items = [x for x in all_items if x["product_id"] in chosen]
                        if not 1 <= len(items) <= 3 or {x["product_id"] for x in items} != chosen:
                            raise Stop("заданные товары пилота не сопоставились однозначно")
                    if len(items) < min(3, result["matched"]):
                        raise Stop("недостаточно позиций с читаемым остатком для пилота")
                result["batches"] = []
                for start in range(0, len(items), 100):
                    chunk = items[start:start + 100]
                    before = own_read(target, warehouse, chunk)
                    expected = {x["ext_key"]: x["qty"] for x in chunk}
                    # У сопоставленной карточки может ещё не быть записи остатка на этом складе.
                    # Не объявляем отсутствие нулём: сохраняем его отдельно и проверяем после PUT.
                    absent = sorted(set(expected) - set(before))
                    batch = {"items": chunk, "before": before, "absent_before": absent,
                             "expected": expected, "status": "prepared"}
                    result["batches"].append(batch)
                    save_private(journal, report)
                    own_send(target, warehouse, chunk)
                    batch["status"] = "sent"
                    save_private(journal, report)
                    for attempt in range(6):
                        after = own_read(target, warehouse, chunk)
                        if after == expected:
                            break
                        time.sleep(10)
                    batch["after"] = after
                    if after != expected:
                        raise Stop("остатки после записи не совпали; см. снимок")
                    batch["status"] = "verified"
                result["status"] = "verified"
                if mode == "pilot":
                    save_private(pilot_path, result)
            except Exception as exc:
                result["status"] = "error"
                error = str(exc)
                for protected in config["targets"]:
                    if protected.get("token"):
                        error = error.replace(protected["token"], "[REDACTED]")
                result["error"] = error[:300]
            save_private(journal, report)
        save_private(own_path("own_stock_" + mode + ".json"), report)
        return report


def _self_check():
    assert physical(2, 1) == 1
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
    assert physical(1, 4) == 0
    assert physical(None, 0) is None
    assert physical("nan", 0) is None
    assert physical("inf", 0) is None
    assert product_id({"meta": {"href": MS_BASE + "/entity/product/abc?expand=supplier"}}) == "abc"
    cards = {"a": {"article": "same", "name": "A", "barcodes": ["123", "124"]},
             "b": {"article": "same", "name": "B", "barcodes": ["456"]}}
    rows = [{"ext_key": "one", "ext_barcode": "123"}, {"ext_key": "one", "ext_barcode": "124"},
            {"ext_key": "zero", "ext_barcode": "456"}, {"ext_key": "foreign", "ext_barcode": "789"}]
    matched, conflicts, unmatched = match_own(cards, {"a": 5, "b": 0}, rows)
    assert [(x["ext_key"], x["qty"]) for x in matched] == [("one", 5), ("zero", 0)]
    assert conflicts == [] and unmatched == ["foreign"]
    # Повтор одного товара на другой площадке не должен подавляться.
    assert len(match_own(cards, {"a": 5, "b": 0}, rows)[0]) == 2
    ambiguous_rows = rows + [{"ext_key": "duplicate", "ext_barcode": "123"}]
    assert {x["ext_key"] for x in match_own(cards, {"a": 5, "b": 0}, ambiguous_rows)[0]} == {"zero"}
    cards["b"]["barcodes"].append("123")
    assert "one" in match_own(cards, {"a": 5, "b": 0}, rows)[1]
    print("stock_push: проверки количества, ID, нулей, нескольких площадок и неоднозначностей прошли")


if __name__ == "__main__":
    _self_check()
