"""Обмен с площадками. Пока SYNC_ENABLED не равен 1, наружу ничего не пишем."""

import os

import config
import core


def customer_order(moment, already):
    return core.plan_customer_order(
        enabled=config.sync_enabled(),
        moment=moment,
        cutover=config.cutover_date(),
        already=already,
    )


def shipment(demand_exists):
    return core.plan_shipment(enabled=config.sync_enabled(), demand_exists=demand_exists)


def fbo(supply_id, already):
    return core.plan_fbo(
        enabled=config.sync_enabled(),
        supply_id=supply_id,
        already=already,
    )


def cancel(demand_exists):
    return core.plan_cancel(enabled=config.sync_enabled(), demand_exists=demand_exists)


def ozon():
    return core.plan_ozon(
        enabled=config.sync_enabled(),
        api_key=os.environ.get("OZON_API_KEY", "").strip(),
    )


def disable_native():
    decision = core.plan_disable_native(
        enabled=config.sync_enabled(),
        confirm=config.confirm_disable_native(),
    )
    if decision != "гасить штатный обмен":
        return decision
    raise RuntimeError("выключение штатного обмена в этот проход не входит")


def push_stock(rows, wb_by_barcode, warehouses):
    if not config.sync_enabled():
        return [], "выключено"
    return core.push_lines(rows, wb_by_barcode, warehouses)


def main():
    print("SYNC_ENABLED", int(config.sync_enabled()))
    print("заказ", customer_order("2026-09-30 09:10:00", False))
    print("отгрузка", shipment(False))
    print("FBO", fbo("supply-1", False))
    print("отмена", cancel(False))
    print("Ozon", ozon())
    print("штатный обмен", disable_native())
    lines, note = push_stock([], {}, ["один"])
    print("пуш", note, len(lines))


if __name__ == "__main__":
    main()
