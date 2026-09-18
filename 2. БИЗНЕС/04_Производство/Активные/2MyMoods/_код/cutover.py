#!/usr/bin/env python3
"""Карта старый этап → новый. Живые сделки не двигает без CUTOVER=ДА и --apply."""

from __future__ import annotations

import argparse
import os

import lib

# Старые id сняты 18.09 с рабочей воронки Продажи 10193806.
MAP = {
    80719358: ("Новая заявка", lib.ST["new"]),
    80719298: ("Взято в работу", lib.ST["in_work"]),
    80719302: ("Хочет заказать / прийти", lib.ST["pay_wait"]),
    80720434: ("Лист ожидания", lib.ST["waitlist"]),
    80721166: ("Запланировал прийти в магазин", lib.ST["visit"]),
    80720958: ("Ожидает оплаты", lib.ST["pay_wait"]),
    80720574: ("Заказ оплачен", lib.ST["paid"]),
    80801814: ("Подтвержден", lib.ST["paid"]),
    80720438: ("Передан на сборку", lib.ST["pack"]),
    80720442: ("Собран", lib.ST["pack"]),
    80720446: ("Самовывоз", lib.ST["pack"]),
    83347502: ("Отправлена часть заказа", lib.ST["sent"]),
    80719966: ("Отправлен", lib.ST["sent"]),
    80719970: ("Доставлен", lib.ST["won"]),
}

SKIP = {
    142: "Выполнен — закрытое, статистику не смешиваем",
    143: "Отменен — закрытое, статистику не смешиваем",
    83347594: "Согласован обмен",
    83347598: "Обмен получен",
    83347602: "Передан в ремонт",
    83347606: "Получен с ремонта",
    83347610: "Обмен отправлен",
    83348254: "Возвращен",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    allow = os.environ.get("CUTOVER") == "ДА"
    amo = lib.Amo()
    leads = amo.iter_leads(f"?filter[pipeline_id]={lib.PIPELINE_SALES_OLD}", pages=40)
    print(f"Живых в старых Продажах (до 10000): {len(leads)}")
    by = {}
    for lead in leads:
        sid = lead.get("status_id")
        by[sid] = by.get(sid, 0) + 1
    move = 0
    hold = 0
    for sid, n in sorted(by.items(), key=lambda x: -x[1]):
        if sid in MAP:
            name, _ = MAP[sid]
            print(f"  {n:5}  {name} → новая воронка")
            move += n
        elif sid in SKIP:
            print(f"  {n:5}  {SKIP[sid]} — не переносим, возвраты отдельно")
            hold += n
        else:
            print(f"  {n:5}  id={sid} — нет в карте, не трогаем")
            hold += n
    print(f"к переносу {move}, оставить {hold}")
    if args.apply and not allow:
        raise SystemExit("Перенос запрещён: нет CUTOVER=ДА. План: только после «да» владельца.")
    if not (args.apply and allow):
        print("Сухой прогон. Входящие не переключал.")
        return
    # Сюда попадём только с явным «да» в env. Пачками по 50.
    batch = []
    for lead in leads:
        sid = lead.get("status_id")
        if sid not in MAP:
            continue
        _, target = MAP[sid]
        batch.append({
            "id": lead["id"],
            "pipeline_id": lib.PIPELINE_SALES_NEW,
            "status_id": target,
        })
        if len(batch) == 50:
            st, _ = amo.req("PATCH", "/api/v4/leads", batch)
            print("patch 50", st)
            batch = []
    if batch:
        st, _ = amo.req("PATCH", "/api/v4/leads", batch)
        print("patch rest", st, len(batch))


if __name__ == "__main__":
    main()
