#!/usr/bin/env python3
"""Передача общей очереди продаж по листу «График». CLI без --apply только читает."""

from __future__ import annotations

import argparse
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lib
import shift_roster

TZ = ZoneInfo("Europe/Moscow")
SALES = {lib.PIPELINE_SALES_NEW, lib.PIPELINE_SALES_OLD}
CLOSED = {142, 143}
MORNING_HOUR = 7


def amo_client() -> lib.Amo:
    env = lib.load_env()
    proxy = env.get("AMOCRM_HTTPS_PROXY") or ""
    if proxy.startswith(("http://127.0.0.1", "http://localhost")):
        parsed = urllib.parse.urlparse(proxy)
        try:
            with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.3):
                pass
        except OSError:
            env["AMOCRM_HTTPS_PROXY"] = ""
    amo = lib.Amo(env)
    if not env.get("AMOCRM_HTTPS_PROXY"):
        amo.opener = urllib.request.build_opener()
    return amo


def runtime_dir() -> Path:
    folder = Path("/opt/2my")
    if folder.is_dir():
        return folder
    return Path("/tmp")


def stamp_path() -> Path:
    return runtime_dir() / "shift_stamp.txt"


BATCH = 50
POLL_SECONDS = 60
RECONCILE_SECONDS = 15 * 60
_next_reconcile = 0.0
_horizon_day = None


def shift_day():
    return (datetime.now(TZ) - timedelta(hours=MORNING_HOUR)).date()


def roster(force=False):
    return shift_roster.duty(shift_day(), force=force)


def records(amo, kind, query):
    seen = set()
    page = 1
    while True:
        code, body = amo.req("GET", f"/api/v4/{kind}?{query}&limit=250&page={page}")
        if code == 204:
            return
        rows = (body.get("_embedded") or {}).get(kind) if isinstance(body, dict) else None
        if code != 200 or not isinstance(rows, list):
            raise RuntimeError(f"{kind}, страница {page}: HTTP {code}, неполная выборка")
        for row in rows:
            key = row["talk_id" if kind == "talks" else "id"]
            if key in seen:
                raise RuntimeError(f"{kind}: данные изменились при постраничном чтении")
            seen.add(key)
            yield row
        if not (body.get("_links") or {}).get("next") and len(rows) < 250:
            return
        if not rows:
            raise RuntimeError(f"{kind}: пустая промежуточная страница")
        page += 1


def batches(ids):
    ordered = sorted(set(ids))
    for pos in range(0, len(ordered), BATCH):
        yield ordered[pos:pos + BATCH]


def id_filter(field, ids):
    return "&".join(f"filter[{field}][{i}]={value}" for i, value in enumerate(ids))


def by_ids(amo, kind, ids, *, unavailable=None, include=None):
    for group in batches(ids):
        query = id_filter("id", group) + (f"&with={include}" if include else "")
        rows = list(records(amo, kind, query))
        received = {row["id"] for row in rows}
        if not received.issubset(group):
            raise RuntimeError(f"{kind}: ответ содержит посторонние ID")
        for missing in set(group) - received:
            code, row = amo.req("GET", f"/api/v4/{kind}/{missing}" + (f"?with={include}" if include else ""))
            if code == 200 and isinstance(row, dict) and row.get("id") == missing:
                rows.append(row)
            elif unavailable is not None and code in (204, 404):
                unavailable.add(missing)
            else:
                raise RuntimeError(f"{kind}: карточка по ID не подтверждена, HTTP {code}")
        yield from rows


def active_sale(row):
    return row["pipeline_id"] in SALES and row["status_id"] not in CLOSED


def sales(amo, on_id, off_id):
    for pipeline in sorted(SALES):
        code, body = amo.req("GET", f"/api/v4/leads/pipelines/{pipeline}")
        if code != 200:
            raise RuntimeError(f"Этапы воронки: HTTP {code}")
        statuses = [s["id"] for s in body["_embedded"]["statuses"]
                    if s["id"] not in CLOSED and s.get("type") == 0]
        if not statuses:
            raise RuntimeError("Не найдены рабочие этапы продаж")
        filters = [id_filter("responsible_user_id", [on_id, off_id]), "with=contacts"]
        for i, status in enumerate(statuses):
            filters.extend([f"filter[statuses][{i}][pipeline_id]={pipeline}",
                            f"filter[statuses][{i}][status_id]={status}"])
        for row in records(amo, "leads", "&".join(filters)):
            if (row["pipeline_id"] != pipeline or row["status_id"] not in statuses
                    or row["responsible_user_id"] not in (on_id, off_id)):
                raise RuntimeError("Ответ amoCRM не соответствует фильтрам продаж")
            yield row


def safe_relations(amo, rows, on_id, off_id, known_sales=()):
    """Контакт или компания могут одновременно обслуживаться другим отделом."""
    refs = {r["id"]: {lead["id"] for lead in r["_embedded"]["leads"]} for r in rows}
    remaining = {lid for lids in refs.values() for lid in lids} - set(known_sales)
    missing = set()
    outside = {lead["id"] for lead in by_ids(amo, "leads", remaining, unavailable=missing)
               if lead["status_id"] not in CLOSED and
               (lead["pipeline_id"] not in SALES or lead["responsible_user_id"] not in (on_id, off_id))}
    return {eid for eid, lids in refs.items() if lids & (outside | missing)}


def plan(amo, on_id, off_id):
    leads = list(sales(amo, on_id, off_id))
    changes = {"contacts": set(), "companies": set(), "tasks": set(), "leads": set()}
    linked = {"contacts": set(), "companies": set()}
    tasks = list(records(amo, "tasks", id_filter("responsible_user_id", [on_id, off_id]) + "&filter[is_completed]=0"))
    active_ids = {lead["id"] for lead in leads}
    closed_ids = {t["entity_id"] for t in tasks if t.get("entity_type") == "leads" and t.get("entity_id")} - active_ids
    missing_parents = set()
    for lead in by_ids(amo, "leads", closed_ids, unavailable=missing_parents, include="contacts"):
        if (lead["pipeline_id"] in SALES and lead["status_id"] in CLOSED
                and lead["responsible_user_id"] in (on_id, off_id)):
            leads.append(lead)
    for task in tasks:
        if task["is_completed"] or task["responsible_user_id"] not in (on_id, off_id):
            raise RuntimeError("Ответ amoCRM не соответствует фильтру задач")
        if task["responsible_user_id"] == off_id:
            changes["tasks"].add(task["id"])
        if task.get("entity_type") in linked and task.get("entity_id"):
            linked[task["entity_type"]].add(task["entity_id"])
    for lead in leads:
        if lead["id"] in active_ids and lead["responsible_user_id"] == off_id:
            changes["leads"].add(lead["id"])
        for kind in linked:
            linked[kind].update(x["id"] for x in lead["_embedded"][kind])

    talk_contacts = set()
    talk_count = 0
    for group in batches(row["id"] for row in leads):
        query = "filter[only_in_work]=1&filter[entity_type]=lead&" + id_filter("entity_id", group)
        for talk in records(amo, "talks", query):
            if talk["entity_id"] not in group or talk["entity_type"] != "lead":
                raise RuntimeError("Ответ amoCRM не соответствует фильтру бесед")
            if talk["is_in_work"]:
                talk_contacts.add(talk["contact_id"])
                talk_count += 1
    linked["contacts"].update(talk_contacts)
    other_chat_owners = set()
    shared = {}
    known_sales = {lead["id"] for lead in leads}
    unavailable = {kind: set() for kind in linked}
    for kind, ids in linked.items():
        rows = list(by_ids(amo, kind, ids, unavailable=unavailable[kind], include="leads"))
        candidates = [row for row in rows if row["responsible_user_id"] == off_id]
        shared[kind] = safe_relations(amo, candidates, on_id, off_id, known_sales)
        for row in rows:
            owner = row["responsible_user_id"]
            if owner == off_id and row["id"] not in shared[kind]:
                changes[kind].add(row["id"])
            elif kind == "contacts" and row["id"] in talk_contacts and owner not in (on_id, off_id):
                other_chat_owners.add(row["id"])

    print(f"Открытые продажи {len(active_ids)}, закрытые с открытой задачей {len(leads) - len(active_ids)}, "
          f"беседы в этих карточках {talk_count}, "
          f"контакты бесед у других сотрудников {len(other_chat_owners)}, "
          f"недоступные контакты {len(unavailable['contacts'])} "
          f"(в беседах {len(unavailable['contacts'] & talk_contacts)}), "
          f"недоступные компании {len(unavailable['companies'])}", flush=True)
    for reason, ids in {**{"shared_" + k: v for k, v in shared.items()},
                        **{"unavailable_" + k: v for k, v in unavailable.items()},
                        "other_chat_owners": other_chat_owners}.items():
        if ids:
            print(f"{reason} ({len(ids)}): {sorted(ids)}", flush=True)
    return changes


def apply_group(amo, kind, ids, on_id, off_id):
    related = kind in ("contacts", "companies")
    rows = list(by_ids(amo, kind, ids, include="leads" if related else None))
    shared = safe_relations(amo, rows, on_id, off_id) if related else set()
    update = [{"id": r["id"], "responsible_user_id": on_id} for r in rows
              if r["responsible_user_id"] == off_id and r["id"] not in shared
              and (kind != "leads" or active_sale(r))
              and (kind != "tasks" or not r["is_completed"])]
    if not update:
        return
    if roster(force=True)[0] != on_id:
        raise RuntimeError("График изменился во время передачи")
    expected = {r["id"] for r in update}
    print(f"Передача {kind} {off_id} -> {on_id}, ID: {sorted(expected)}", flush=True)
    code, body = amo.req("PATCH", f"/api/v4/{kind}", update)
    if code != 200:
        raise RuntimeError(f"Передача {kind}: HTTP {code}")
    acknowledged = body.get("_embedded", {}).get(kind, []) if isinstance(body, dict) else []
    if {r["id"] for r in acknowledged} != expected:
        raise RuntimeError(f"amoCRM не подтвердила весь пакет {kind}")
    if any(r["responsible_user_id"] != on_id for r in by_ids(amo, kind, expected)):
        raise RuntimeError(f"{kind}: владелец после записи не подтверждён")


def nudge_lead(amo: lib.Amo, lead: dict) -> int | None:
    on_id, _ = roster()
    off_id = shift_roster.other(on_id)
    if not off_id or not active_sale(lead) or lead["responsible_user_id"] != off_id:
        return None
    # Вебхук мог прийти до ручной смены владельца или закрытия.
    lead_id = lead["id"]
    try:
        apply_group(amo, "leads", [lead_id], on_id, off_id)
        current, = by_ids(amo, "leads", [lead_id])
        if not active_sale(current) or current["responsible_user_id"] != on_id:
            return None
    except RuntimeError as exc:
        print(f"Передачу сделки {lead_id} проверит очередная сверка: {exc}", flush=True)
        return None
    try:
        query = f"filter[entity_type]=leads&filter[entity_id]={lead_id}&filter[is_completed]=0"
        tasks = [r["id"] for r in records(amo, "tasks", query)
                 if r["entity_type"] == "leads" and r["entity_id"] == lead_id
                 and r["responsible_user_id"] == off_id and not r["is_completed"]]
        for group in batches(tasks):
            apply_group(amo, "tasks", group, on_id, off_id)
    except RuntimeError as exc:
        print(f"Задачи сделки {lead_id} повторно проверит сверка: {exc}", flush=True)
    return on_id


def run(apply: bool, pretend: int | None = None) -> str:
    if pretend is not None and (apply or not shift_roster.other(pretend)):
        raise ValueError("Подмена смены разрешена только для просмотра одного из двух менеджеров")
    on_id, why = (pretend, "предпросмотр") if pretend else roster(force=True)
    off_id = shift_roster.other(on_id)
    print(f"График: {why}", flush=True)
    if not off_id:
        return "skip"
    amo = amo_client()
    status, account = amo.req("GET", "/api/v4/account")
    if status != 200 or account.get("subdomain") != "2my":
        raise RuntimeError("Аккаунт 2MY не подтверждён")
    status, user = amo.req("GET", f"/api/v4/users/{on_id}")
    if status != 200 or user.get("rights", {}).get("is_active") is not True:
        raise RuntimeError("Дежурный пользователь amoCRM не подтверждён")
    changes = plan(amo, on_id, off_id)
    print("К передаче:", {kind: len(ids) for kind, ids in changes.items()}, flush=True)
    if apply and any(changes.values()):
        for kind, ids in changes.items():
            for group in batches(ids):
                apply_group(amo, kind, group, on_id, off_id)
        remaining = plan(amo, on_id, off_id)
        print("Остаток:", {kind: len(ids) for kind, ids in remaining.items()}, flush=True)
        if any(remaining.values()):
            raise RuntimeError("Повторное чтение обнаружило непереданные карточки")
    if apply and roster(force=True)[0] != on_id:
        raise RuntimeError("К концу проверки график изменился")
    return "ok"


def maybe_morning() -> float:
    global _next_reconcile, _horizon_day
    day = shift_day()
    if _horizon_day != day:
        try:
            shift_roster.ensure_horizon()
            _horizon_day = day
        except Exception as exc:
            print(f"График не продлился: {type(exc).__name__}", flush=True)
    on_id, why = roster()
    stamp = f"{day.isoformat()} {on_id}" if on_id else f"{day.isoformat()} skip {why}"
    path = stamp_path()
    previous = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if not on_id:
        if stamp != previous:
            print(f"Передача пропущена: {why}", flush=True)
            path.write_text(stamp, encoding="utf-8")
        _next_reconcile = 0
    elif stamp != previous or time.monotonic() >= _next_reconcile:
        try:
            if run(apply=True) == "ok" and day == shift_day() and roster()[0] == on_id:
                path.write_text(stamp, encoding="utf-8")
                _next_reconcile = time.monotonic() + RECONCILE_SECONDS
        except Exception as exc:
            print(f"Передача не подтверждена, будет повтор: {exc}", flush=True)
    return time.time() + POLL_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--publish", action="store_true", help="Собрать пустой лист с галочками")
    action.add_argument("--preview", type=int, choices=[lib.USER_KRISTINA, lib.USER_TANYA])
    args = parser.parse_args()
    if args.publish:
        print(f"Лист готов, дней {shift_roster.publish()}")
    else:
        run(apply=args.apply, pretend=args.preview)


if __name__ == "__main__":
    main()
