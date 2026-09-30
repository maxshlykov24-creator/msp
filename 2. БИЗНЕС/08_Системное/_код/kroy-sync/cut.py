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


def post_fact(date, product_name, color, sizes, rolls, apply):
    mismatch = core.check_color(product_name, color)
    if mismatch:
        return mismatch
    sizes = core.parse_sizes(sizes)
    if sum(sizes.values()) < 1:
        return "штуки не указаны"
    key = core.fact_key(date, product_name, sizes, rolls)
    loss_code = "kroy-%s-loss" % key
    enter_code = "kroy-%s-enter" % key
    loss = ms_api.find_doc("loss", loss_code)
    enter = ms_api.find_doc("enter", enter_code)
    product = find_yuji(product_name)
    if not product:
        return "изделие не найдено в YUJI"
    roll = ms_api.find_product_by_article(core.roll_article(color))
    if not roll:
        return "карточки рулона нет"
    store_id = os.environ.get("MS_STORE_ID", "").strip()
    org_id = os.environ.get("MS_ORG_ID", "").strip()
    if not store_id or not org_id:
        return "нет склада или организации"
    report = ms_api.stock_on_store(store_id, roll["id"])
    roll_stock = None if report is None else report.get("stock")
    decision = core.plan_cut(
        roll_stock=roll_stock,
        rolls=rolls,
        loss_exists=bool(loss),
        enter_exists=bool(enter),
    )
    if decision != "провести":
        return decision
    if not apply:
        return "можно провести %s, режим просмотра" % key
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
            return "нет размера %s" % size
        positions.append({
            "quantity": qty,
            "assortment": {"meta": ms_api.meta("variant", variant["id"])},
        })
    moment = date + " 12:00:00"
    org = {"meta": ms_api.meta("organization", org_id)}
    store = {"meta": ms_api.meta("store", store_id)}
    if not loss:
        loss = ms_api.request("POST", "/entity/loss", {
            "organization": org,
            "store": store,
            "externalCode": loss_code,
            "moment": moment,
            "description": "Факт кроя " + product_name,
            "positions": [{
                "quantity": int(rolls),
                "assortment": {"meta": ms_api.meta("product", roll["id"])},
            }],
        })
    try:
        if not enter:
            ms_api.request("POST", "/entity/enter", {
                "organization": org,
                "store": store,
                "externalCode": enter_code,
                "moment": moment,
                "description": "Факт кроя " + product_name,
                "positions": positions,
            })
    except ms_api.MsError:
        if loss and loss.get("id"):
            ms_api.delete_doc("loss", loss["id"])
        raise
    return "проведено " + key


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
