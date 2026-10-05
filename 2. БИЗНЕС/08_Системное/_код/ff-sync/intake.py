"""Приёмка: список штрихкодов → сверка с кабинетами → товары в МойСклад."""

import math
import re

import registry
from db import (
    QUEUE_STATES,
    add_intake_row,
    barcode_norm,
    delete_supply,
    find_cache,
    find_cache_group,
    get_client_by_id,
    get_intake_row,
    init_db,
    insert_lot,
    insert_supply,
    list_cabinets,
    list_intake,
    prefer_hit,
    run_lock,
    update_intake_row,
    upsert_sku,
)
from ms import kind_from_subject, tracking_code
from products_push import create_purchase_order, gtin14, push_one

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
BARCODE_RE = re.compile(r"(OZN[A-Z0-9]+|\d{8,14})", re.I)


def now_iso():
    return datetime.now(MSK).isoformat()


def parse_num(raw, default=None):
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return default
    try:
        value = float(text)
        return value if math.isfinite(value) else default
    except ValueError:
        return default


DIMS_RE = re.compile(r"^\s*(\d+[.,]?\d*)\s*[x×хX*]\s*(\d+[.,]?\d*)\s*[x×хX*]\s*(\d+[.,]?\d*)\s*$")


def parse_liters(raw):
    """Литры числом или габаритами в сантиметрах: 8x8x120 → 7.68 л.

    Возвращает (литры, подпись габаритов). Подпись пустая, если ввели число.
    """
    text = str(raw or "").strip()
    if not text:
        return None, ""
    hit = DIMS_RE.match(text)
    if hit:
        a, b, c = (float(x.replace(",", ".")) for x in hit.groups())
        return round(a * b * c / 1000.0, 4), "%g×%g×%g" % (a, b, c)
    return parse_num(text), ""


def parse_file(name, data):
    name = (name or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xls") or name.endswith(".xlsm"):
        import registry

        rows = registry.read_rows(name, data)
        start = next((i for i, row in enumerate(rows) if registry.is_header(row)), -1)
        if start >= 0:
            cols = registry.map_columns(rows[start])
            items = []
            for row in rows[start + 1 :]:
                item = registry.position(row, cols)
                if item is None:
                    if items:
                        break
                    continue
                items.append(item)
            if items:
                return items
        import io
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        bits = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                bits.append(" ".join("" if v is None else str(v) for v in row))
        text = "\n".join(bits)
        items = parse_items(text)
        have = {barcode_norm(i["barcode"]) for i in items if i.get("barcode")}
        for hit in BARCODE_RE.findall(text):
            key = barcode_norm(hit)
            if key in have:
                continue
            have.add(key)
            items.append({"barcode": hit, "article": "", "name": "", "qty": None})
        return items
    text = data.decode("utf-8-sig", errors="replace")
    return parse_items(text)


def _is_qty(raw):
    text = str(raw or "").strip().replace(",", ".")
    if not text or not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return False
    digits = text.split(".", 1)[0]
    return len(digits) < 8


def _as_item(body, qty):
    body = str(body or "").strip()
    if body.endswith(".0") and body[:-2].isdigit():
        body = body[:-2]
    hit = BARCODE_RE.fullmatch(body)
    if hit:
        return {"barcode": hit.group(0), "article": "", "name": "", "qty": qty}
    if not body or _is_qty(body):
        return None
    return {"barcode": "", "article": body, "name": "", "qty": qty}


def parse_items(text):
    """Позиции из текста. Количество в строке необязательно.

    «2043339560409» — позиция без количества.
    «2043339560409 12» или «2043339560409;12» — сразу с количеством.
    Строка без штрихкода считается артикулом.
    """
    found = []
    seen = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        chunks = [line]
        bits = [b.strip() for b in line.split(",") if b.strip()]
        if len(bits) > 1 and all(BARCODE_RE.fullmatch(b) for b in bits):
            chunks = bits
        elif len(bits) == 2 and BARCODE_RE.fullmatch(bits[0]) and _is_qty(bits[1]):
            chunks = ["%s %s" % (bits[0], bits[1])]
        for chunk in chunks:
            qty = None
            body = chunk.strip()
            matched = re.match(r"^(.+?)[;\t ]+(\d+(?:[.,]\d+)?)$", body)
            if matched and _is_qty(matched.group(2)):
                body = matched.group(1).strip()
                qty = float(matched.group(2).replace(",", "."))
            item = _as_item(body, qty)
            if not item:
                continue
            key = barcode_norm(item["barcode"]) or item["article"].upper()
            if not key:
                continue
            if key in seen:
                prev = seen[key]
                if item["qty"] is not None:
                    prev["qty"] = (prev["qty"] or 0) + item["qty"]
                continue
            seen[key] = item
            found.append(item)
    return found


def parse_barcodes(text):
    return [item["barcode"] for item in parse_items(text) if item["barcode"]]


def lookup(client_id, barcode, article="", hits=None):
    if hits is None:
        # Введённый штрихкод сильнее артикула. Если код не нашёлся, не
        # подставляем карточку по артикулу с другим штрихкодом.
        hits = (find_cache_group(client_id, barcode=barcode) if barcode
                else find_cache_group(client_id, article=article or None))
    if not hits:
        return None
    first = prefer_hit(hits)[0]
    subject = (first["subject"] if "subject" in first.keys() else "") or ""
    name = first["name"] or ""
    gtin = ""
    for hit in prefer_hit(hits):
        val = (hit["gtin"] if "gtin" in hit.keys() else "") or ""
        if val:
            gtin = val
            break
    if not gtin:
        from catalog_pull import real_gtin

        gtin = real_gtin(barcode) or real_gtin(first["ext_barcode"])
    kind = (first["tracking_type"] if "tracking_type" in first.keys() else "") or ""
    if not kind:
        need = first["need_kiz"] if "need_kiz" in first.keys() else None
        kind = kind_from_subject((subject + " " + name).strip(), need_kiz=None if need is None else bool(need))
    return {
        "hits": hits,
        "article": first["ext_article"] or "",
        "name": name,
        "marketplace": "+".join(sorted({h["marketplace"] for h in hits if h["marketplace"]})),
        "gtin": gtin or "",
        "tracking_type": kind,
        "subject": subject,
    }


def article_groups(client_id, article):
    """Карточки с этим артикулом, разложенные по штрихкодам.

    У WB один артикул носят все размеры товара, поэтому несколько штрихкодов на
    один артикул — это несколько разных товаров, а не одна карточка.
    """
    buckets = {}
    for hit in find_cache(client_id, article=str(article or "").strip()):
        buckets.setdefault(barcode_norm(hit["ext_barcode"]), []).append(hit)
    return buckets


def match_registry(client_id, barcode, article):
    """Строка реестра → карточки кабинетов. Возвращает (hits, спорно, пояснение).

    Штрихкод — надёжный ключ. Артикул берём только запасным и только когда он
    ведёт ровно на один штрихкод: иначе гадать нельзя, отдаём строку оператору.
    """
    if barcode:
        hits = find_cache_group(client_id, barcode=barcode)
        if hits:
            return hits, "", ""
        if article and article_groups(client_id, article):
            return [], "штрихкод не совпадает с карточкой артикула %s — выбери товар" % article, ""
        return [], "", ""
    article = str(article or "").strip()
    if not article:
        return [], "", ""
    buckets = article_groups(client_id, article)
    codes = [code for code in buckets if code]
    if len(codes) > 1:
        return (
            [hit for code in codes for hit in buckets[code]],
            "артикул %s ведёт на разные товары — выбери нужный" % article,
            "",
        )
    if codes:
        return find_cache_group(client_id, barcode=codes[0]), "", "сопоставлено по артикулу %s" % article
    flat = [hit for group in buckets.values() for hit in group]
    if flat:
        return flat, "", "сопоставлено по артикулу %s, штрихкода в кабинете нет" % article
    return [], "", ""


def group_norms(client_id, barcode, article=""):
    hits = (find_cache_group(client_id, barcode=barcode) if barcode
            else find_cache_group(client_id, article=article or None))
    return {barcode_norm(h["ext_barcode"]) for h in hits if h["ext_barcode"]}, (hits[0]["gtin"] if hits and hits[0]["gtin"] else "")


def find_queued_same(client_id, barcode, gtin, article=""):
    """Уже в очереди тот же товар: тот же штрихкод, GTIN, артикул или карточка WB+Ozon."""
    norm = barcode_norm(barcode)
    norms, _g = group_norms(client_id, barcode)
    norms.add(norm)
    art = str(article or "").strip().upper()
    for row in list_intake(states=QUEUE_STATES):
        if row["client_id"] != client_id:
            continue
        if norm and barcode_norm(row["barcode"]) in norms:
            return row
        if gtin and row["gtin"] and row["gtin"] == gtin:
            return row
        # без штрихкода единственная зацепка — артикул клиента
        if not norm and art and str(row["article"] or "").strip().upper() == art:
            return row
        qnorm, _ = group_norms(client_id, row["barcode"], row["article"] or "")
        if norm and norm in qnorm:
            return row
    return None


def need_of(liters, pick_rate, qty):
    need = []
    liters_val = parse_num(liters)
    pick_val = parse_num(pick_rate)
    qty_val = parse_num(qty)
    if liters_val is None or liters_val <= 0:
        need.append("литраж")
    if pick_val is None or pick_val < 0:
        need.append("сборку, ₽/шт")
    if qty_val is None or qty_val <= 0:
        need.append("количество")
    return need


def decide(found, liters, kind, gtin, pick_rate=None, qty=None):
    if not found:
        return "warn", "нет в кабинетах этого клиента"
    need = need_of(liters, pick_rate, qty)
    if need:
        return "warn", "нужно: " + ", ".join(need)
    if not kind:
        return "warn", "укажи тип продукции"
    if not gtin and tracking_code(kind) != "NOT_TRACKED":
        return "warn", "нужен GTIN"
    return "draft", ""


def fields_of(client_id, barcode, liters=None, kind="", gtin="", pick_rate=None, qty=None, dims="", hits=None, fallback=None):
    """fallback — артикул и наименование от клиента: нужны, когда кабинеты товар не знают."""
    fb = fallback or {}
    found = lookup(client_id, barcode, article=fb.get("article") or "", hits=hits)
    if not found:
        found = {"article": "", "name": "", "marketplace": "", "gtin": gtin14(barcode), "tracking_type": "", "hits": []}
    kind = (kind or "").strip() or found.get("tracking_type") or ""
    gtin = str(gtin or "").strip() or found.get("gtin") or ""
    state, note = decide(found if found.get("hits") else None, liters, kind, gtin, pick_rate, qty)
    return {
        "barcode": barcode,
        "article": found.get("article") or fb.get("article") or "",
        "name": found.get("name") or fb.get("name") or "",
        "marketplace": found.get("marketplace") or "",
        "gtin": gtin,
        "tracking_type": kind,
        "liters": liters,
        "dims": dims or "",
        "pick_rate": pick_rate,
        "qty": qty or 0,
        "state": state,
        "note": note,
    }


def refresh_client_catalog(client_id):
    from catalog_pull import pull_one

    notes = []
    cabinets = [cab for cab in list_cabinets() if cab["client_id"] == client_id and cab["active"]]
    if not cabinets:
        return ["нет активного кабинета: проверь токены контрагента в МойСклад и обнови клиентов"]
    for cab in cabinets:
        try:
            n, err = pull_one(cab)
            notes.append("%s %s" % (cab["marketplace"], err or ("%s товаров" % n)))
        except Exception as exc:
            notes.append("%s: %s" % (cab["marketplace"], exc))
    return notes


def add_many(client_id, barcodes, liters=None, kind="", author="", refresh=True, pick_rate=None, qty=None, count=False):
    client = get_client_by_id(int(client_id))
    if not client:
        raise ValueError("клиент не найден")
    items = _items_of(barcodes, qty)
    if not items:
        raise ValueError("нет позиций")
    pulled = []
    if refresh:
        pulled = refresh_client_catalog(client["id"])
    added = []
    skipped = []
    updated = []
    found = 0
    missing = 0
    liters_val, dims_val = parse_liters(liters)
    pick_val = parse_num(pick_rate)
    if pick_val is None:
        pick_val = parse_num(client["tariff_pick"])
    for item in items:
        result = _place_item(
            client["id"],
            item,
            liters_val,
            kind,
            pick_val,
            dims_val,
            author,
            count=count,
        )
        if result["kind"] == "updated":
            updated.append(result["row"])
        elif result["kind"] == "skipped":
            skipped.append(result["row"])
        else:
            added.append(result["row"])
            if result["row"]["note"] == "нет в кабинетах этого клиента":
                missing += 1
            else:
                found += 1
    return {
        "added": added,
        "skipped": skipped,
        "updated": updated,
        "found": found,
        "missing": missing,
        "pulled": pulled,
    }


def _items_of(barcodes, qty):
    """Текст, список штрихкодов или уже разобранные строки файла."""
    fallback = parse_num(qty)
    if isinstance(barcodes, str):
        items = parse_items(barcodes)
    else:
        items = []
        for raw in barcodes or []:
            if isinstance(raw, dict):
                items.append(
                    {
                        "barcode": str(raw.get("barcode") or "").strip(),
                        "article": str(raw.get("article") or "").strip(),
                        "name": str(raw.get("name") or "").strip(),
                        "qty": raw.get("qty"),
                    }
                )
            else:
                items.extend(parse_items(str(raw)))
    out = []
    for item in items:
        if not item.get("barcode") and not item.get("article"):
            continue
        if item.get("qty") is None:
            item["qty"] = fallback
        out.append(item)
    return out


def _place_item(client_id, item, liters, kind, pick_rate, dims, author, count=False):
    """Новая позиция, запись количества или +1 со сканера.

    Без числа позиция встаёт в очередь с пустым количеством. Повтор с числом
    записывает его. Сканер (count) прибавляет к уже лежащей строке.
    """
    code = str(item.get("barcode") or "").strip()
    article = str(item.get("article") or "").strip()
    name = str(item.get("name") or "").strip()
    line_qty = item.get("qty")
    hits = None
    clash = ""
    hint = ""
    if not code and article:
        hits, clash, hint = match_registry(client_id, "", article)
        if hits and not clash:
            code = prefer_hit(hits)[0]["ext_barcode"] or ""
    fields = fields_of(
        client_id,
        code,
        liters,
        kind,
        pick_rate=pick_rate,
        qty=line_qty if line_qty else (1 if count else None),
        dims=dims,
        hits=hits or None,
        fallback={"article": article, "name": name},
    )
    if clash:
        fields["state"] = "clash"
        fields["note"] = clash
    elif hint and fields["note"]:
        fields["note"] = "%s · %s" % (hint, fields["note"])
    elif hint:
        fields["note"] = hint
    same = find_queued_same(client_id, code or fields.get("barcode") or "", fields.get("gtin") or "", article or fields.get("article") or "")
    if same and count:
        step = float(line_qty) if line_qty else 1
        patched = patch(same["id"], qty=float(same["qty"] or 0) + step)
        return {
            "kind": "updated",
            "row": {"id": same["id"], "barcode": patched["barcode"] or code or article, "qty": patched["qty"], "name": patched["name"], "note": patched["note"]},
        }
    if same and line_qty:
        patched = patch(same["id"], qty=line_qty)
        return {
            "kind": "updated",
            "row": {"id": same["id"], "barcode": patched["barcode"] or code or article, "qty": patched["qty"], "name": patched["name"], "note": patched["note"]},
        }
    if same:
        mps = "+".join(
            sorted(
                {
                    p
                    for p in ((same["marketplace"] or "") + "+" + (fields.get("marketplace") or "")).split("+")
                    if p
                }
            )
        )
        if mps and mps != (same["marketplace"] or ""):
            update_intake_row(same["id"], marketplace=mps)
        return {"kind": "skipped", "row": {"barcode": code or article, "note": "тот же товар уже в очереди"}}
    rid = add_intake_row(client_id, fields, now_iso(), author)
    return {
        "kind": "added",
        "row": {"id": rid, "barcode": fields["barcode"] or article, "state": fields["state"], "note": fields["note"], "name": fields["name"], "qty": fields["qty"] or 0},
    }


def add_registry(client_id, filename, data, author="", refresh=True, pick_rate=None):
    """Реестр клиента xlsx → строки очереди, привязанные к карточке поставки."""
    client = get_client_by_id(int(client_id))
    if not client:
        raise ValueError("клиент не найден")
    parsed = registry.parse(filename, data)
    pulled = refresh_client_catalog(client["id"]) if refresh else []
    pick_val = parse_num(pick_rate)
    if pick_val is None:
        pick_val = parse_num(client["tariff_pick"])
    supply_id = insert_supply(
        client["id"],
        parsed["supply"],
        filename or "",
        len(parsed["rows"]),
        parsed["qty_total"],
        now_iso(),
        author,
    )
    added = []
    skipped = []
    updated = []
    clashes = 0
    matched = 0
    for item in parsed["rows"]:
        hits, clash, hint = match_registry(client["id"], item["barcode"], item["article"])
        same = find_queued_same(client["id"], item["barcode"], "", item["article"])
        if same:
            if item["qty"]:
                patched = patch(same["id"], qty=item["qty"])
                updated.append(
                    {
                        "id": same["id"],
                        "article": item["article"],
                        "barcode": item["barcode"] or patched["barcode"],
                        "qty": patched["qty"],
                    }
                )
            else:
                skipped.append(
                    {
                        "article": item["article"],
                        "barcode": item["barcode"],
                        "note": "тот же товар уже в очереди, строка %s" % same["id"],
                    }
                )
            continue
        fields = fields_of(
            client["id"],
            item["barcode"],
            pick_rate=pick_val,
            qty=item["qty"],
            hits=hits or None,
            fallback={"article": item["article"], "name": item["name"]},
        )
        if clash:
            fields["state"] = "clash"
            fields["note"] = clash
            clashes += 1
        elif hits:
            matched += 1
            if hint and fields["note"]:
                fields["note"] = "%s · %s" % (hint, fields["note"])
            elif hint:
                fields["note"] = hint
        rid = add_intake_row(client["id"], fields, now_iso(), author, supply_id=supply_id)
        added.append(
            {
                "id": rid,
                "article": item["article"],
                "barcode": item["barcode"],
                "qty": item["qty"],
                "state": fields["state"],
                "note": fields["note"],
            }
        )
    if not added:
        # весь реестр оказался дублем: карточка поставки без строк только путает
        delete_supply(supply_id)
        supply_id = None
    return {
        "supply_id": supply_id,
        "supply": parsed["supply"],
        "added": added,
        "skipped": skipped,
        "updated": updated,
        "matched": matched,
        "clashes": clashes,
        "missing": sum(1 for r in added if r["note"] == "нет в кабинетах этого клиента"),
        "merged": parsed["merged"],
        "problems": parsed["problems"],
        "pulled": pulled,
    }


def candidates(row_id):
    """Варианты товара для спорной строки: по артикулу клиента, по одному на штрихкод."""
    row = get_intake_row(row_id)
    if not row:
        raise ValueError("строка не найдена")
    out = []
    for code, group in article_groups(row["client_id"], row["article"]).items():
        first = prefer_hit(group)[0]
        out.append(
            {
                "barcode": first["ext_barcode"] or code,
                "name": first["name"] or "",
                "size": (first["size"] if "size" in first.keys() else "") or "",
                "marketplace": "+".join(sorted({h["marketplace"] for h in group if h["marketplace"]})),
                "gtin": (first["gtin"] if "gtin" in first.keys() else "") or "",
            }
        )
    return sorted(out, key=lambda x: (x["size"], x["barcode"]))


def resolve(row_id, barcode):
    """Оператор выбрал товар для спорной строки: фиксируем штрихкод и пересчитываем."""
    row = get_intake_row(row_id)
    if not row:
        raise ValueError("строка не найдена")
    code = str(barcode or "").strip()
    if not code:
        raise ValueError("не выбран товар")
    if not find_cache_group(row["client_id"], barcode=code):
        raise ValueError("штрихкод %s не найден в кабинетах клиента" % code)
    fields = fields_of(
        row["client_id"],
        code,
        row["liters"],
        row["tracking_type"] or "",
        "",
        row["pick_rate"] if "pick_rate" in row.keys() else None,
        row["qty"],
        (row["dims"] if "dims" in row.keys() else "") or "",
        fallback={"article": row["article"], "name": row["name"]},
    )
    update_intake_row(
        row_id,
        barcode=code,
        article=fields["article"],
        name=fields["name"],
        marketplace=fields["marketplace"],
        gtin=fields["gtin"],
        tracking_type=fields["tracking_type"],
        state=fields["state"],
        note=fields["note"],
    )
    return get_intake_row(row_id)


def add(client_id, barcode, qty, liters, kind, gtin="", author=""):
    res = add_many(client_id, [barcode], liters=liters, kind=kind, author=author, refresh=False, qty=qty)
    if res["skipped"]:
        raise ValueError(res["skipped"][0]["note"])
    if not res["added"]:
        raise ValueError("не добавил строку")
    if gtin:
        patch(res["added"][0]["id"], gtin=gtin)
    return res["added"][0]["id"]


def patch(row_id, liters=None, kind=None, gtin=None, pick_rate=None, qty=None):
    row = get_intake_row(row_id)
    if not row:
        raise ValueError("строка не найдена")
    old_dims = (row["dims"] if "dims" in row.keys() else "") or ""
    if liters is None:
        liters_val, dims_val = row["liters"], old_dims
    else:
        liters_val, dims_val = parse_liters(liters)
    kind_val = row["tracking_type"] if kind is None else (kind or "").strip()
    gtin_val = row["gtin"] if gtin is None else str(gtin or "").strip()
    pick_val = row["pick_rate"] if pick_rate is None else parse_num(pick_rate)
    qty_val = row["qty"] if qty is None else parse_num(qty)
    fields = fields_of(
        row["client_id"], row["barcode"], liters_val, kind_val, gtin_val, pick_val, qty_val, dims_val,
        fallback={"article": row["article"], "name": row["name"]},
    )
    if row["state"] == "clash":
        # литраж и сборку править можно, но товар всё ещё не выбран
        fields["state"] = "clash"
        fields["note"] = row["note"]
    update_intake_row(
        row_id,
        article=fields["article"],
        name=fields["name"],
        marketplace=fields["marketplace"],
        gtin=fields["gtin"],
        tracking_type=fields["tracking_type"],
        liters=fields["liters"],
        dims=fields["dims"],
        pick_rate=fields["pick_rate"],
        qty=fields["qty"],
        state=fields["state"],
        note=fields["note"],
    )
    return get_intake_row(row_id)


def recheck(row):
    liters = row["liters"]
    pick_rate = row["pick_rate"] if "pick_rate" in row.keys() else None
    kind = row["tracking_type"] or ""
    need = need_of(liters, pick_rate, row["qty"])
    if need:
        return None, "нужно: " + ", ".join(need)
    found = lookup(row["client_id"], row["barcode"], row["article"] or "")
    if not found:
        return None, "нет в кабинетах этого клиента"
    gtin = (row["gtin"] or "").strip() or found["gtin"]
    kind = kind or found["tracking_type"]
    if not kind:
        return None, "укажи тип продукции"
    if not gtin and tracking_code(kind) != "NOT_TRACKED":
        return None, "нужен GTIN"
    first = prefer_hit(found["hits"])[0]
    payload = {
        "cabinet_id": first["cabinet_id"],
        "ext_key": first["ext_key"],
        "ext_article": first["ext_article"],
        "ext_barcode": row["barcode"] or first["ext_barcode"],
        "name": first["name"],
        "size": first["size"],
        "Литраж_л": liters,
        "Тип продукции": kind,
        "GTIN": gtin,
    }
    return {"payload": payload, "hits": found["hits"], "gtin": gtin, "marketplace": found["marketplace"]}, ""


def process_row(row):
    prepared, err = recheck(row)
    if err:
        update_intake_row(row["id"], state="warn", note=err)
        return None, err
    res = push_one(prepared["payload"], hits=prepared["hits"], dry=False)
    if not res["ok"]:
        update_intake_row(row["id"], state="warn", note=res["msg"])
        return None, res["msg"]
    for hit in prepared["hits"]:
        upsert_sku(
            row["client_id"],
            hit["cabinet_id"],
            hit["marketplace"],
            hit["ext_key"],
            hit["ext_article"],
            hit["ext_barcode"],
            res["id"],
        )
    update_intake_row(
        row["id"],
        article=res["article"],
        name=prepared["payload"]["name"],
        marketplace=prepared["marketplace"],
        gtin=prepared["gtin"],
        ms_product_id=res["id"],
        note=res["msg"],
    )
    return {"client": res["client"], "product_id": res["id"], "article": res["article"]}, ""


def run(blocking=True, client_id=None):
    init_db()
    with run_lock(blocking=blocking):
        return _run(client_id=client_id)


def _run(client_id=None):
    rows = list_intake(states=("draft", "warn"))
    if client_id is not None:
        rows = [row for row in rows if row["client_id"] == int(client_id)]
    done = []
    skipped = []
    pending = {}
    for row in rows:
        extra, err = process_row(row)
        if err:
            skipped.append({"id": row["id"], "barcode": row["barcode"], "note": err})
            print("приёмка %s: %s" % (row["barcode"], err))
            continue
        bucket = pending.setdefault(
            extra["client"]["id"], {"client": extra["client"], "items": [], "rows": []}
        )
        bucket["items"].append({"product_id": extra["product_id"], "qty": float(row["qty"])})
        bucket["rows"].append((row, extra))
    orders = []
    for bucket in pending.values():
        oid, number, err = create_purchase_order(bucket["client"], bucket["items"])
        if err:
            for row, _extra in bucket["rows"]:
                update_intake_row(row["id"], state="warn", note=err)
                skipped.append({"id": row["id"], "barcode": row["barcode"], "note": err})
            print("заказ поставщика: %s" % err)
            continue
        label = number or oid
        orders.append({"client": bucket["client"]["name"], "number": label, "ms_id": oid})
        for row, extra in bucket["rows"]:
            insert_lot(
                row["client_id"],
                extra["product_id"],
                extra["article"],
                row["barcode"],
                row["gtin"],
                row["name"],
                row["tracking_type"],
                row["liters"],
                float(row["qty"]),
                now_iso(),
                oid,
                row["pick_rate"] if "pick_rate" in row.keys() else None,
                row["dims"] if "dims" in row.keys() else "",
            )
            update_intake_row(row["id"], state="done", ms_order_name=label, note="заказ поставщика %s" % label)
            done.append({"id": row["id"], "barcode": row["barcode"], "order": label})
            print("приёмка %s: заказ поставщика %s" % (row["barcode"], label))
    return {"done": done, "skipped": skipped, "orders": orders}


if __name__ == "__main__":
    print(run())
