"""Факт кроя: списание рулона и приход размеров одним внешним ключом.

По умолчанию только показывает, что сделает. --apply пишет в МойСклад, на площадки не ходит.
"""

import argparse
import os

import config
import core
import ms_api


def variants_of(product_id):
    import urllib.parse
    path = "/entity/variant?filter=" + urllib.parse.quote("productid=" + product_id)
    return ms_api.rows(path)


def find_yuji(name):
    for product in ms_api.rows("/entity/product"):
        if product.get("pathName") == core.YUJI_FOLDER and product.get("name") == name:
            return product
    return None


def apply_fact(date, product_name, color, sizes, rolls, apply, external_key=None, force=False):
    """Провести факт. force проводит списание, даже если рулонов на складе меньше факта."""
    result = {
        "ok": False,
        "text": "",
        "loss_name": "",
        "enter_name": "",
        "roll_name": "",
        "roll_before": None,
    }

    def finish(text, ok=False):
        result["text"] = text
        result["ok"] = ok
        return result

    mismatch = core.check_color(product_name, color)
    if mismatch:
        return finish(mismatch)
    sizes = core.parse_sizes(sizes)
    if sum(sizes.values()) < 1:
        return finish("штуки не указаны")
    key = external_key or core.fact_key(date, product_name, sizes, rolls)
    loss_code = "kroy-%s-loss" % key
    enter_code = "kroy-%s-enter" % key
    loss = ms_api.find_doc("loss", loss_code)
    enter = ms_api.find_doc("enter", enter_code)
    product = find_yuji(product_name)
    if not product:
        return finish("изделие не найдено в YUJI")
    roll = ms_api.find_product_by_article(core.roll_article(color))
    if not roll:
        return finish("карточки рулона нет")
    result["roll_name"] = roll.get("name") or ""
    store_id = os.environ.get("MS_STORE_ID", "").strip()
    org_id = os.environ.get("MS_ORG_ID", "").strip()
    if not store_id or not org_id:
        return finish("нет склада или организации")
    report = ms_api.stock_on_store(store_id, roll["id"])
    # Строки в отчёте нет, пока рулон ни разу не приходовали. Это ноль, не обрыв чтения.
    roll_stock = 0 if report is None else report.get("stock")
    result["roll_before"] = roll_stock
    decision = core.plan_cut(
        roll_stock=roll_stock,
        rolls=rolls,
        loss_exists=bool(loss),
        enter_exists=bool(enter),
    )
    short = decision == "рулонов не хватает"
    if short and force:
        decision = "провести"
    if decision == "уже проведено":
        result["loss_name"] = (loss or {}).get("name") or ""
        result["enter_name"] = (enter or {}).get("name") or ""
        return finish(decision, ok=True)
    if decision != "провести":
        return finish(decision)
    if not apply:
        text = "можно провести %s, режим просмотра" % key
        if short:
            text += ", рулонов на складе меньше"
        return finish(text)
    by_size = {}
    for variant in variants_of(product["id"]):
        for char in variant.get("characteristics") or []:
            if char.get("name") == "Размер":
                by_size[char.get("value")] = variant
    positions = []
    for size, qty in sizes.items():
        if qty < 1:
            continue
        variant = by_size.get(size)
        if not variant:
            return finish("нет размера %s" % size)
        positions.append({
            "quantity": qty,
            "assortment": {"meta": ms_api.meta("variant", variant["id"])},
        })
    moment = date + " 12:00:00"
    org = {"meta": ms_api.meta("organization", org_id)}
    store = {"meta": ms_api.meta("store", store_id)}
    note = "Приёмка кроя %s" % product_name
    if not loss:
        loss = ms_api.request("POST", "/entity/loss", {
            "organization": org,
            "store": store,
            "applicable": True,
            "externalCode": loss_code,
            "moment": moment,
            "description": "Списание рулона по приёмке. %s" % note,
            "positions": [{
                "quantity": int(rolls),
                "assortment": {"meta": ms_api.meta("product", roll["id"])},
            }],
        })
    try:
        if not enter:
            enter = ms_api.request("POST", "/entity/enter", {
                "organization": org,
                "store": store,
                "applicable": True,
                "externalCode": enter_code,
                "moment": moment,
                "description": note,
                "positions": positions,
            })
    except ms_api.MsError:
        if loss and loss.get("id") and not ms_api.find_doc("enter", enter_code):
            ms_api.delete_doc("loss", loss["id"])
        raise
    result["loss_name"] = (loss or {}).get("name") or ""
    result["enter_name"] = (enter or {}).get("name") or ""
    text = "проведено " + key
    if short:
        text += ", рулонов на складе было меньше факта"
    return finish(text, ok=True)


def post_fact(date, product_name, color, sizes, rolls, apply, external_key=None, force=False):
    return apply_fact(
        date, product_name, color, sizes, rolls, apply,
        external_key=external_key, force=force,
    )["text"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--color", required=True)
    parser.add_argument("--s", default=0)
    parser.add_argument("--m", default=0)
    parser.add_argument("--l", default=0)
    parser.add_argument("--rolls", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(post_fact(
        args.date,
        args.product,
        args.color,
        {"S": args.s, "M": args.m, "L": args.l},
        args.rolls,
        args.apply,
    ))


if __name__ == "__main__":
    main()
