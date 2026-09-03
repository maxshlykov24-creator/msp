#!/usr/bin/env python3
"""
Сумма штук (quantity) по позициям документов маркировки за календарные годы.

Метрика: для каждого документа суммируются поля quantity по всем его позициям, затем эти суммы
складываются по году. Это общее количество единиц товара (штук), а не число строк позиций.

Сверка вручную: можно взять вывод скрипта как «всего по API за год» и вычесть известные вам
недошедшие количества — получится контрольный остаток без привязки к статусам в интерфейсе.

Правильный тип для журнала «Ввод в оборот кодов маркировки» в веб-интерфейсе — enrollorder
(см. URL: #enrollorder/edit … moduleName=CrptOrder). Это не emissionorder («Заказ кодов маркировки» / СУЗ).

  MOYSKLAD_ENTITY — тип документа API (по умолчанию enrollorder).
  Для старого сценария заказа кодов в СУЗ: MOYSKLAD_ENTITY=emissionorder

Фильтр по статусу документа (опционально):
  MOYSKLAD_DOC_STATE_FIELD — имя поля в JSON документа, например documentState (emissionorder).
  MOYSKLAD_DOCUMENT_STATES — допустимые значения через запятую.
  Если MOYSKLAD_DOC_STATE_FIELD не задан:
    • для emissionorder по умолчанию поле documentState и значение SUZ_COMPLETED;
    • для enrollorder фильтра по статусу нет (суммируются все документы за год) — задайте поле
      после просмотра GET …/entity/enrollorder/<id> (статусы обмена «Принят» / «В обработке»).

  MOYSKLAD_POSITION_STATUSES — опционально enum позиции (для emissionorder часто EMISSION_*).
  MOYSKLAD_WORKERS, MOYSKLAD_THROTTLE_SEC — см. ниже.

Пример:
  export MOYSKLAD_TOKEN='...'
  python3 Бизнес/99_Системное/moysklad_marking_pieces_by_year.py
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

BASE = "https://api.moysklad.ru/api/remap/1.2"
SESSION = requests.Session()


def api_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    url = BASE + path
    delay = 0.5
    for attempt in range(8):
        r = SESSION.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {os.environ['MOYSKLAD_TOKEN']}",
                "Accept": "application/json;charset=utf-8",
                "Accept-Encoding": "gzip",
            },
            timeout=120,
        )
        if r.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 1.6, 30.0)
            continue
        if r.status_code >= 400:
            try:
                err = r.json()
            except Exception:
                err = r.text
            if r.status_code == 403 and isinstance(err, dict):
                for e in err.get("errors") or []:
                    if e.get("code") == 1061:
                        raise RuntimeError(
                            "Нет доступа к JSON API (код 1061). В МойСклад: пользователь должен иметь "
                            "право на API; у отдельных сущностей (enrollorder) — нужная тарифная опция "
                            "маркировки и доступ к документу."
                        ) from None
            r.raise_for_status()
        return r.json()
    raise RuntimeError("Превышено число повторов при 429 Too Many Requests")


def iter_documents(entity: str, moment_from: str, moment_to: str):
    offset = 0
    limit = 1000
    flt = f"moment>={moment_from};moment<={moment_to}"
    while True:
        data = api_get(
            f"/entity/{entity}",
            {"limit": str(limit), "offset": str(offset), "filter": flt},
        )
        if "errors" in data:
            raise RuntimeError(data["errors"])
        rows = data.get("rows") or []
        for row in rows:
            yield row
        meta = data.get("meta") or {}
        if offset + len(rows) >= int(meta.get("size") or 0):
            break
        offset += limit


def fetch_positions(entity: str, doc_id: str) -> list[dict[str, Any]]:
    data = api_get(f"/entity/{entity}/{doc_id}/positions")
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return list(data.get("rows") or [])


def resolve_state_filter(entity: str) -> tuple[str | None, set[str] | None]:
    """
    Returns (state_field_name or None, allowed_values or None).
    None, None = не фильтровать по статусу документа.
    """
    explicit_field = os.environ.get("MOYSKLAD_DOC_STATE_FIELD", "").strip()
    states_raw = os.environ.get("MOYSKLAD_DOCUMENT_STATES", "").strip()

    if explicit_field:
        if not states_raw:
            print(
                "Задайте MOYSKLAD_DOCUMENT_STATES вместе с MOYSKLAD_DOC_STATE_FIELD",
                file=sys.stderr,
            )
            sys.exit(1)
        allowed = {s.strip() for s in states_raw.split(",") if s.strip()}
        return explicit_field, allowed

    if entity == "emissionorder":
        default_states = states_raw or "SUZ_COMPLETED"
        allowed = {s.strip() for s in default_states.split(",") if s.strip()}
        return "documentState", allowed

    # enrollorder и прочие: без фильтра по умолчанию
    if states_raw:
        print(
            "Для фильтра по статусу задайте также MOYSKLAD_DOC_STATE_FIELD "
            "(посмотрите поля в GET /entity/…/id).",
            file=sys.stderr,
        )
        sys.exit(1)
    return None, None


def sum_year(
    entity: str,
    year: int,
    state_field: str | None,
    allowed_states: set[str] | None,
    position_status_filter: set[str] | None,
    workers: int,
) -> tuple[float, int, int]:
    """Returns (sum_qty, orders_matched, orders_skipped_state)."""
    mf = f"{year}-01-01 00:00:00"
    mt = f"{year}-12-31 23:59:59"
    orders = list(iter_documents(entity, mf, mt))

    def doc_ok(o: dict[str, Any]) -> bool:
        if not state_field or allowed_states is None:
            return True
        val = o.get(state_field)
        if val is None:
            return False
        if isinstance(val, str):
            return val in allowed_states
        # раскрытое meta — не поддерживаем автоматически
        return False

    matched = [o for o in orders if doc_ok(o)]
    skipped = len(orders) - len(matched)

    total_qty = 0.0
    if not matched:
        return 0.0, 0, skipped

    def work(o: dict[str, Any]) -> float:
        time.sleep(float(os.environ.get("MOYSKLAD_THROTTLE_SEC", "0.08")))
        oid = o["id"]
        rows = fetch_positions(entity, oid)
        s = 0.0
        for p in rows:
            st = p.get("status") or ""
            if position_status_filter is not None and st not in position_status_filter:
                continue
            s += float(p.get("quantity") or 0)
        return s

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, o) for o in matched]
        for fut in as_completed(futs):
            total_qty += fut.result()

    return total_qty, len(matched), skipped


def main() -> None:
    if not os.environ.get("MOYSKLAD_TOKEN"):
        print("Задайте MOYSKLAD_TOKEN", file=sys.stderr)
        sys.exit(1)

    entity = os.environ.get("MOYSKLAD_ENTITY", "enrollorder").strip() or "enrollorder"
    state_field, allowed_states = resolve_state_filter(entity)

    ps = os.environ.get("MOYSKLAD_POSITION_STATUSES", "").strip()
    position_status_filter: set[str] | None
    if ps:
        position_status_filter = {s.strip() for s in ps.split(",") if s.strip()}
    else:
        position_status_filter = None

    workers = max(1, int(os.environ.get("MOYSKLAD_WORKERS", "2")))

    years = [2024, 2025]
    sf_note = (
        f"поле {state_field!r}, значения {{{', '.join(sorted(allowed_states or []))}}}"
        if state_field
        else "без фильтра по статусу документа"
    )
    print(
        f"Документы: entity/{entity}\n"
        f"{sf_note}\n"
        "Сумма: Σ quantity по позициям (дата документа moment — календарный год).\n"
    )
    for y in years:
        qty, n_match, n_skip = sum_year(
            entity, y, state_field, allowed_states, position_status_filter, workers
        )
        print(f"{y}: {qty:g} шт.  (подошло документов: {n_match}, отсеяно по статусу: {n_skip})")


if __name__ == "__main__":
    main()
