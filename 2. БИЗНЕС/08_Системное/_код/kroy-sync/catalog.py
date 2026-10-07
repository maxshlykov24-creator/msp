"""Рулоны по цветам папки YUJI. Остаток рулонов не ставим: его называет клиент."""

import core
import ms_api


def meter_uom_id():
    for row in ms_api.rows("/entity/uom"):
        if row.get("name") == "м":
            return row.get("id") or ""
    return ""


def use_meters():
    """Рулон списывается метрами длины, не штуками."""
    uom_id = meter_uom_id()
    if not uom_id:
        return 0
    changed = 0
    for product in ms_api.rows("/entity/product"):
        if product.get("pathName") != core.ROLL_FOLDER:
            continue
        current = (((product.get("uom") or {}).get("meta") or {}).get("href") or "")
        if current.rstrip("/").endswith(uom_id):
            continue
        ms_api.request("PUT", "/entity/product/" + product["id"], {
            "uom": {"meta": ms_api.meta("uom", uom_id)},
        })
        changed += 1
    return changed


def yuji_colors():
    colors = []
    seen = set()
    for product in ms_api.rows("/entity/product"):
        if product.get("pathName") != core.YUJI_FOLDER:
            continue
        color = core.color_of(product.get("name") or "")
        if not color or color in seen:
            continue
        seen.add(color)
        colors.append(color)
    return colors


def ensure_rolls(apply):
    """Создаёт карточки рулонов без количества. Повтор не плодит дубли."""
    colors = yuji_colors()
    folder = None
    made = []
    skipped = []
    if apply:
        folder = ms_api.ensure_folder(core.ROLL_FOLDER)
    uom = meter_uom_id()
    for color in colors:
        article = core.roll_article(color)
        existing = ms_api.find_product_by_article(article) if apply else None
        if existing:
            skipped.append(color)
            continue
        if not apply:
            made.append(color)
            continue
        body = {
            "name": "Рулон " + color,
            "article": article,
            "code": article,
            "productFolder": {"meta": ms_api.meta("productfolder", folder["id"])},
        }
        if uom:
            body["uom"] = {"meta": ms_api.meta("uom", uom)}
        ms_api.request("POST", "/entity/product", body)
        made.append(color)
    return {"colors": colors, "created_or_planned": made, "already": skipped, "apply": apply}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = ensure_rolls(args.apply)
    print("цветов", len(result["colors"]))
    print("новые" if args.apply else "будут созданы", len(result["created_or_planned"]))
    print("уже были", len(result["already"]))
    if not args.apply:
        print("режим просмотра, в МойСклад не писал")


if __name__ == "__main__":
    main()
