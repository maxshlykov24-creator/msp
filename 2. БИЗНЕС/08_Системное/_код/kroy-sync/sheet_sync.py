"""Норма метров в таблице. Приёмка живёт в Моём Складе, сервер только списывает рулон.

На площадки не ходит.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

import catalog
import config
import core
import ms_api

SHEET_ID = "1FaVgAGM_RIDcy6PGl0lYIGQuTsQG3H2Fqnf0lHLAiUA"
MAP_SHEET = "Сопоставление"
OLD_SHEET = "Приёмка"
HEADER = ["Изделие", "Рулон", "S, м на 1 шт", "M, м на 1 шт", "L, м на 1 шт"]
NOTE = (
    "Сколько метров рулона этого цвета уходит на одну штуку размера. "
    "Рулон определяется по цвету в названии изделия. "
    "Когда в Моём Складе проводят приёмку или оприходование, сервер списывает эту длину. "
    "Пустая ячейка значит нормы ещё нет: такую позицию не списываем."
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
KIND_NAME = {"supply": "приёмке", "enter": "оприходованию"}
_token = {"value": "", "until": 0}


def sheet_id():
    return os.environ.get("SHEET_ID", "").strip() or SHEET_ID


def meter(value):
    """Метры из ячейки. Пусто это отсутствие нормы, не ноль."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value == "":
            return None
    number = float(value)
    if number < 0:
        raise ValueError("метры меньше нуля")
    return number


def ru(number):
    text = ("%.3f" % float(number)).rstrip("0").rstrip(".")
    return text.replace(".", ",")


def loss_text(kind, doc_name, plan):
    lines = ["Списание метров по %s %s." % (KIND_NAME.get(kind, "документу"), doc_name or "")]
    for row in plan["details"]:
        lines.append("%s, %s, %s шт, %s м на штуку, %s м" % (
            row["product"], row["size"], ru(row["qty"]), ru(row["per"]), ru(row["meters"]),
        ))
    for color, meters in sorted(plan["by_color"].items()):
        lines.append("Итого Рулон %s: %s м" % (color, ru(meters)))
    return "\n".join(lines)


def _creds_token():
    now = time.time()
    if _token["value"] and _token["until"] > now + 30:
        return _token["value"]
    path = os.environ.get("GOOGLE_SA_PATH", "").strip()
    if not path:
        raise RuntimeError("нет GOOGLE_SA_PATH")
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    creds.refresh(Request())
    _token["value"] = creds.token
    _token["until"] = now + 3000
    return creds.token


def _api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://sheets.googleapis.com/v4/spreadsheets" + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + _creds_token(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("таблица ответила %s: %s" % (exc.code, detail)) from exc


def _meta():
    return _api("GET", "/" + sheet_id() + "?fields=sheets.properties")


def _sheet_id_by_title(title):
    for sheet in _meta().get("sheets") or []:
        props = sheet.get("properties") or {}
        if props.get("title") == title:
            return props.get("sheetId")
    return None


def _values(range_name):
    quoted = urllib.parse.quote(range_name, safe="")
    query = urllib.parse.urlencode({
        "valueRenderOption": "UNFORMATTED_VALUE",
        "majorDimension": "ROWS",
    })
    data = _api("GET", "/%s/values/%s?%s" % (sheet_id(), quoted, query))
    return data.get("values") or []


def _write(range_name, values):
    quoted = urllib.parse.quote(range_name, safe="")
    _api(
        "PUT",
        "/%s/values/%s?valueInputOption=USER_ENTERED" % (sheet_id(), quoted),
        {"range": range_name, "majorDimension": "ROWS", "values": values},
    )


def _cell(row, index):
    return row[index] if index < len(row) else ""


def mapping_rows(saved):
    rows = []
    for product in ms_api.rows("/entity/product"):
        if product.get("pathName") != core.YUJI_FOLDER:
            continue
        name = product.get("name") or ""
        color = core.color_of(name)
        roll = ("Рулон " + color) if color else ""
        meters = saved.get(name) or ["", "", ""]
        rows.append([name, roll, meters[0], meters[1], meters[2]])
    rows.sort(key=lambda item: item[0])
    return rows


def setup():
    """Лист нормы. Лист приёмки убираем: приёмка живёт в Моём Складе."""
    titles = {}
    for sheet in _meta().get("sheets") or []:
        props = sheet.get("properties") or {}
        titles[props.get("title")] = props.get("sheetId")
    requests = []
    if MAP_SHEET not in titles:
        requests.append({"addSheet": {"properties": {"title": MAP_SHEET}}})
    if requests:
        _api("POST", "/" + sheet_id() + ":batchUpdate", {"requests": requests})
        titles = {}
        for sheet in _meta().get("sheets") or []:
            props = sheet.get("properties") or {}
            titles[props.get("title")] = props.get("sheetId")
    if OLD_SHEET in titles and len(titles) > 1:
        _api("POST", "/" + sheet_id() + ":batchUpdate", {
            "requests": [{"deleteSheet": {"sheetId": titles[OLD_SHEET]}}],
        })
    header = _values(MAP_SHEET + "!A2:F2")
    saved = {}
    header_cells = [str(cell) for cell in (header[0] if header else [])]
    if "Цвет" in header_cells:
        meter_indexes = (3, 4, 5)
    else:
        meter_indexes = (2, 3, 4)
    if "Артикул" not in header_cells and "S, м на 1 шт" in header_cells:
        for raw in _values(MAP_SHEET + "!A3:F"):
            name = str(_cell(raw, 0) or "").strip()
            if not name:
                continue
            meters = []
            for index in meter_indexes:
                value = _cell(raw, index)
                if isinstance(value, str) and value.strip().upper().startswith("ROL-"):
                    value = ""
                meters.append(value)
            saved[name] = meters
    catalog_rows = mapping_rows(saved)
    map_id = _sheet_id_by_title(MAP_SHEET)
    _api("POST", "/" + sheet_id() + ":batchUpdate", {"requests": [
        {"unmergeCells": {"range": {"sheetId": map_id}}},
    ]})
    _write(MAP_SHEET + "!A1", [[NOTE]])
    _write(MAP_SHEET + "!A2:E2", [HEADER])
    _write(MAP_SHEET + "!F2:F2", [[""]])
    if catalog_rows:
        _write(MAP_SHEET + "!A3:E%d" % (len(catalog_rows) + 2), catalog_rows)
        _write(MAP_SHEET + "!F3:F%d" % (len(catalog_rows) + 2), [[""]] * len(catalog_rows))
    map_id = _sheet_id_by_title(MAP_SHEET)
    _api("POST", "/" + sheet_id() + ":batchUpdate", {"requests": [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": map_id, "gridProperties": {"frozenRowCount": 2}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": map_id, "startRowIndex": 1, "endRowIndex": 2},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": map_id,
                    "startRowIndex": 2,
                    "endRowIndex": max(len(catalog_rows) + 2, 3),
                    "startColumnIndex": 2,
                    "endColumnIndex": 5,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.###"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
    ]})
    changed = catalog.use_meters()
    return len(catalog_rows), changed


def norms():
    found = {}
    for raw in _values(MAP_SHEET + "!A3:E"):
        name = str(_cell(raw, 0) or "").strip()
        if not name:
            continue
        found[name] = {
            "S": meter(_cell(raw, 2)),
            "M": meter(_cell(raw, 3)),
            "L": meter(_cell(raw, 4)),
        }
    return found


def state_path():
    return config.ROOT / "state.json"


def load_state():
    path = state_path()
    if not path.is_file():
        data = {"watch_from": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "done": [], "noted": []}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data, True
    return json.loads(path.read_text(encoding="utf-8")), False


def save_state(data):
    state_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _store_id(doc):
    href = (((doc.get("store") or {}).get("meta") or {}).get("href") or "")
    return href.rstrip("/").split("/")[-1]


def _variant(variant_id, cache):
    if variant_id in cache:
        return cache[variant_id]
    data = ms_api.request("GET", "/entity/variant/" + variant_id)
    product_href = ((data.get("product") or {}).get("meta") or {}).get("href") or ""
    product_id = product_href.rstrip("/").split("/")[-1]
    product = ms_api.request("GET", "/entity/product/" + product_id) if product_id else {}
    size = ""
    for char in data.get("characteristics") or []:
        if char.get("name") == "Размер":
            size = char.get("value") or ""
    cache[variant_id] = (product.get("name") or "", product.get("pathName") or "", size)
    return cache[variant_id]


def lines_of(kind, doc, cache):
    data = ms_api.request("GET", "/entity/%s/%s/positions" % (kind, doc["id"]))
    lines = []
    for row in data.get("rows") or []:
        href = (((row.get("assortment") or {}).get("meta") or {}).get("href") or "")
        if "/variant/" not in href:
            continue
        variant_id = href.rstrip("/").split("/")[-1].split("?")[0]
        name, path, size = _variant(variant_id, cache)
        if path != core.YUJI_FOLDER:
            continue
        lines.append((name, size, row.get("quantity") or 0))
    return lines


def incoming(kind, watch_from):
    filt = urllib.parse.quote("moment>%s;applicable=true" % watch_from)
    return ms_api.rows("/entity/%s?order=moment,asc&filter=%s" % (kind, filt))


def _note_missing(kind, doc, missing):
    line = "Крой: нет нормы метров для %s." % "; ".join(missing)
    old = doc.get("description") or ""
    if line in old:
        return
    text = (old + "\n" + line).strip()
    ms_api.request("PUT", "/entity/%s/%s" % (kind, doc["id"]), {"description": text})


def _create_loss(kind, doc, plan):
    code = "kroy-%s-%s-loss" % (kind, doc["id"])
    existing = ms_api.find_doc("loss", code)
    if existing:
        return existing
    positions = []
    for color, meters in sorted(plan["by_color"].items()):
        if meters <= 0:
            continue
        roll = ms_api.find_product_by_article(core.roll_article(color))
        if not roll:
            raise ms_api.MsError("нет карточки рулона " + color)
        positions.append({
            "quantity": meters,
            "assortment": {"meta": ms_api.meta("product", roll["id"])},
        })
    if not positions:
        return None
    return ms_api.request("POST", "/entity/loss", {
        "organization": doc.get("organization"),
        "store": doc.get("store"),
        "applicable": True,
        "externalCode": code,
        "moment": doc.get("moment"),
        "description": loss_text(kind, doc.get("name"), plan),
        "positions": positions,
    })


def process_once():
    """Новые приёмки и оприходования после запуска. История не списывается."""
    state, first = load_state()
    if first:
        print("старт с %s, старые документы не трогаю" % state["watch_from"], flush=True)
        return 0
    store_id = os.environ.get("MS_STORE_ID", "").strip()
    done = set(state.get("done") or [])
    noted = set(state.get("noted") or [])
    posted = 0
    cache = {}
    table = norms()
    for kind in ("supply", "enter"):
        for doc in incoming(kind, state["watch_from"]):
            key = "%s:%s" % (kind, doc.get("id"))
            if key in done:
                continue
            if doc.get("externalCode", "").startswith("kroy-"):
                done.add(key)
                continue
            if store_id and _store_id(doc) != store_id:
                continue
            lines = lines_of(kind, doc, cache)
            if not lines:
                done.add(key)
                continue
            plan = core.plan_consumption(lines, table)
            if plan["missing"]:
                if key not in noted:
                    _note_missing(kind, doc, plan["missing"])
                    noted.add(key)
                    print("%s %s ждёт норму: %s" % (
                        kind, doc.get("name"), "; ".join(plan["missing"]),
                    ), flush=True)
                continue
            loss = _create_loss(kind, doc, plan)
            done.add(key)
            noted.discard(key)
            posted += 1
            print("%s %s списано %s" % (
                kind, doc.get("name"), (loss or {}).get("name") or "без метров",
            ), flush=True)
    state["done"] = sorted(done)
    state["noted"] = sorted(noted)
    save_state(state)
    return posted


def main():
    if "--setup" in sys.argv:
        count, changed = setup()
        print("норма готова, изделий %s, рулонов переведено в метры %s" % (count, changed))
        return
    if "--once" in sys.argv:
        print("списаний: %s" % process_once())
        return
    pause = int(os.environ.get("POLL_SECONDS", "15") or "15")
    while True:
        try:
            process_once()
        except Exception as exc:
            print("проход не удался: %s" % exc, flush=True)
        time.sleep(pause)


if __name__ == "__main__":
    main()
