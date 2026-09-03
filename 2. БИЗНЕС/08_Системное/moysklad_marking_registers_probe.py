#!/usr/bin/env python3
"""
Быстрая сверка: сколько документов в разных типах за год (по полю moment в фильтре).

Зачем: журнал «Ввод в оборот кодов маркировки» в веб-интерфейсе соответствует типу enrollorder
(URL вида #enrollorder/edit&moduleName=CrptOrder), а не emissionorder (заказ кодов в СУЗ).

Как узнать тип в API: фрагмент URL после # до /edit — имя сущности для /entity/<имя>.

  export MOYSKLAD_TOKEN='...'
  python3 2. БИЗНЕС/08_Системное/moysklad_marking_registers_probe.py

Переменные:
  MOYSKLAD_YEAR — по умолчанию 2025
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests

BASE = "https://api.moysklad.ru/api/remap/1.2"


def get_json(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    r = requests.get(
        BASE + path,
        params=params,
        headers={
            "Authorization": f"Bearer {os.environ['MOYSKLAD_TOKEN']}",
            "Accept": "application/json;charset=utf-8",
            "Accept-Encoding": "gzip",
        },
        timeout=120,
    )
    data = r.json()
    if r.status_code >= 400 and "errors" not in data:
        r.raise_for_status()
    return data


def count_entity(etype: str, year: int) -> tuple[int | None, str | None]:
    flt = f"moment>={year}-01-01 00:00:00;moment<={year}-12-31 23:59:59"
    data = get_json(f"/entity/{etype}", {"limit": "1", "offset": "0", "filter": flt})
    if "errors" in data:
        msg = str(data["errors"])
        if "moment" in msg and "неизвестное поле" in msg:
            data2 = get_json(f"/entity/{etype}", {"limit": "1", "offset": "0"})
            if "errors" in data2:
                return None, str(data2["errors"])
            n = int((data2.get("meta") or {}).get("size") or 0)
            return n, f"(без фильтра по году: для {etype!r} нет фильтра moment в API)"
        return None, msg
    return int((data.get("meta") or {}).get("size") or 0), None


def main() -> None:
    if not os.environ.get("MOYSKLAD_TOKEN"):
        print("Задайте MOYSKLAD_TOKEN", file=sys.stderr)
        sys.exit(1)
    year = int(os.environ.get("MOYSKLAD_YEAR", "2025"))
    print(f"Год: {year} (фильтр по moment, если поле поддерживается типом документа)\n")
    for et in ("enrollorder", "emissionorder", "crptorder", "retireorder", "enter", "supply"):
        n, err = count_entity(et, year)
        if err and n is None:
            print(f"{et:16} — ошибка: {err[:280]}")
        elif err:
            print(f"{et:16} — всего документов: {n}  {err}")
        else:
            print(f"{et:16} — всего документов за год (moment): {n}")


if __name__ == "__main__":
    main()
