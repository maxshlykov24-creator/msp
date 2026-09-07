"""Отчёт + бэкфилл чекбоксов «Запланирован визит» / «Визит состоялся».

Разово проходит по ВСЕМ сделкам воронки «Продажи» (независимо от
ответственного, в отличие от основного коллектора), созданным с заданной
даты, восстанавливает по истории событий `lead_status_changed`, доходила ли
сделка когда-либо до статусов «Запланирован визит» (settings.pipeline
status STAGE_NAMES[82003654]) и «Визит состоялся» (STAGE_NAMES[82004138]),
и проставляет соответствующие чекбоксы (settings.field_visit_planned /
settings.field_visit_done) в amoCRM, если они ещё не стоят.

Этапы визита достраиваются той же логикой, что и на дашборде
(`collector.with_implied_visits`): Успех и «Покупка согласована» означают, что
визит был, даже если менеджер протащил сделку мимо этапов (типично для
«Сарафана»).

Запуск (внутри контейнера, где есть .env с AMO_ACCESS_TOKEN):
    python -m scripts.backfill_visits            # dry-run — только отчёт
    python -m scripts.backfill_visits --apply     # реально пишет в amoCRM

Отчёт печатается в stdout и не требует БД дашборда — все данные берутся
напрямую из amoCRM (независимая проверка, не зависит от LeadSnapshot,
который ограничен тремя менеджерами дашборда).
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from app.amo_client import AmoClient, AmoError
from app.collector import with_implied_visits
from app.config import settings

log = logging.getLogger("backfill_visits")

MSK = ZoneInfo("Europe/Moscow")

SINCE = datetime(2026, 5, 1, 0, 0, 0, tzinfo=MSK)

STATUS_PLANNED = 82003654  # «Запланирован визит»
STATUS_VISITED = 82004138  # «Визит состоялся»

FIELD_PLANNED = settings.field_visit_planned
FIELD_VISITED = settings.field_visit_done

BATCH_SIZE = 250


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _cfv_bool(lead: dict, field_id: int) -> bool:
    for cf in lead.get("custom_fields_values") or []:
        if cf.get("field_id") == field_id:
            values = cf.get("values") or []
            if values:
                v = values[0].get("value")
                if isinstance(v, bool):
                    return v
                return str(v).strip().lower() in ("1", "true", "да")
    return False


def _fetch_all_pipeline_leads(amo: AmoClient, since: datetime) -> list[dict]:
    params: dict[str, object] = {
        "filter[pipeline_id]": settings.pipeline_sales,
        "filter[created_at][from]": _ts(since),
        "limit": 250,
        "with": "custom_fields_values",
    }
    return list(amo.paginate("/api/v4/leads", "leads", params))


def _fetch_status_events(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    params = {
        "filter[entity][0]": "lead",
        "filter[type][0]": "lead_status_changed",
        "filter[created_at][from]": from_ts,
        "filter[created_at][to]": to_ts,
        "limit": 250,
    }
    try:
        return list(amo.paginate("/api/v4/events", "events", params))
    except AmoError as exc:
        log.warning("events fetch %s..%s: %s", from_ts, to_ts, exc)
        return []


def _build_stages_by_lead(events: list[dict], lead_ids: set[int]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for e in events:
        lead_id = e.get("entity_id")
        if lead_id not in lead_ids:
            continue
        changes = e.get("value_after") or []
        if not changes:
            continue
        status_id = ((changes[0] or {}).get("lead_status") or {}).get("id")
        if status_id:
            out[lead_id].add(status_id)
    return out


def analyze(amo: AmoClient) -> tuple[list[dict], dict[int, set[int]]]:
    log.info("тянем сделки воронки «Продажи» с %s...", SINCE.date())
    leads = _fetch_all_pipeline_leads(amo, SINCE)
    log.info("сделок найдено: %d", len(leads))
    lead_ids = {l["id"] for l in leads if l.get("id")}

    now_ts = _ts(datetime.now(MSK))
    events = _fetch_status_events(amo, _ts(SINCE), now_ts)
    log.info("событий lead_status_changed в окне: %d", len(events))
    stages_by_lead = _build_stages_by_lead(events, lead_ids)
    return leads, stages_by_lead


def report(leads: list[dict], stages_by_lead: dict[int, set[int]]) -> dict:
    total = len(leads)
    both = only_planned = only_visited = neither = 0
    already_planned = already_visited = 0
    to_set_planned: list[int] = []
    to_set_visited: list[int] = []

    for lead in leads:
        lid = lead["id"]
        reached = with_implied_visits(stages_by_lead.get(lid, set()) | {lead.get("status_id")})
        r_planned = STATUS_PLANNED in reached
        r_visited = STATUS_VISITED in reached

        if r_planned and r_visited:
            both += 1
        elif r_planned:
            only_planned += 1
        elif r_visited:
            only_visited += 1
        else:
            neither += 1

        cur_planned = _cfv_bool(lead, FIELD_PLANNED)
        cur_visited = _cfv_bool(lead, FIELD_VISITED)
        if cur_planned:
            already_planned += 1
        if cur_visited:
            already_visited += 1

        if r_planned and not cur_planned:
            to_set_planned.append(lid)
        if r_visited and not cur_visited:
            to_set_visited.append(lid)

    return {
        "total": total,
        "reached_planned": both + only_planned,
        "reached_visited": both + only_visited,
        "both": both,
        "only_planned": only_planned,
        "only_visited": only_visited,
        "neither": neither,
        "already_planned": already_planned,
        "already_visited": already_visited,
        "to_set_planned": to_set_planned,
        "to_set_visited": to_set_visited,
    }


def print_report(rep: dict) -> None:
    print("=" * 60)
    print(f"Сделки воронки «Продажи» с {SINCE.date()}: {rep['total']}")
    print("-" * 60)
    print(f"Дошли до «Запланирован визит» (когда-либо):  {rep['reached_planned']}")
    print(f"Дошли до «Визит состоялся» (когда-либо):     {rep['reached_visited']}")
    print(f"  — оба этапа:                                {rep['both']}")
    print(f"  — только «Запланирован визит»:               {rep['only_planned']}")
    print(f"  — только «Визит состоялся»:                  {rep['only_visited']}")
    print(f"  — ни один из этапов:                         {rep['neither']}")
    print("-" * 60)
    print(f"Чекбокс «Запланирован визит» (field {FIELD_PLANNED}) уже стоит: {rep['already_planned']}")
    print(f"Чекбокс «Визит состоялся» (field {FIELD_VISITED}) уже стоит:    {rep['already_visited']}")
    print("-" * 60)
    print(f"Будет проставлено «Запланирован визит»: {len(rep['to_set_planned'])}")
    print(f"Будет проставлено «Визит состоялся»:     {len(rep['to_set_visited'])}")
    print("=" * 60)


def apply_backfill(amo: AmoClient, rep: dict) -> tuple[int, int]:
    updates: dict[int, dict] = {}
    for lid in rep["to_set_planned"]:
        updates.setdefault(lid, {"id": lid, "custom_fields_values": []})
        updates[lid]["custom_fields_values"].append(
            {"field_id": FIELD_PLANNED, "values": [{"value": True}]}
        )
    for lid in rep["to_set_visited"]:
        updates.setdefault(lid, {"id": lid, "custom_fields_values": []})
        updates[lid]["custom_fields_values"].append(
            {"field_id": FIELD_VISITED, "values": [{"value": True}]}
        )

    items = list(updates.values())
    n_ok = 0
    for i in range(0, len(items), BATCH_SIZE):
        chunk = items[i:i + BATCH_SIZE]
        amo.patch_batch("/api/v4/leads", chunk)
        n_ok += len(chunk)
        log.info("PATCH batch %d..%d ok", i, i + len(chunk))
    return len(rep["to_set_planned"]), len(rep["to_set_visited"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    apply_mode = "--apply" in sys.argv

    if not settings.amo_access_token:
        log.error("AMO_ACCESS_TOKEN пуст")
        sys.exit(1)

    amo = AmoClient()
    try:
        leads, stages_by_lead = analyze(amo)
        rep = report(leads, stages_by_lead)
        print_report(rep)

        if apply_mode:
            n_planned, n_visited = apply_backfill(amo, rep)
            print(f"\nПрименено: «Запланирован визит» → {n_planned} сделок, «Визит состоялся» → {n_visited} сделок.")
        else:
            print("\nDRY-RUN: изменения в amoCRM НЕ применены. Запустите с --apply, чтобы записать.")
    finally:
        amo.close()


if __name__ == "__main__":
    main()
