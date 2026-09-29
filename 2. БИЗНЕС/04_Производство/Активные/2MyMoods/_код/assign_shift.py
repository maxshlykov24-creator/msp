#!/usr/bin/env python3
"""Смена по графику Кристины и Тани.

Утром переносит открытые задачи с той, кто сегодня не работает, на ту, кто работает.
Закрытые задачи не трогает. Сделку переносит только если на ней висит такая задача,
или если новая заявка и новое сообщение пришли не на того.

По умолчанию показывает план. Запись в amo только с --apply.
"""

from __future__ import annotations

import argparse
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime
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


def open_tasks(amo: lib.Amo, user_id: int) -> list[dict]:
    items: list[dict] = []
    page = 1
    while page <= 20:
        st, body = amo.req(
            "GET",
            f"/api/v4/tasks?filter[responsible_user_id]={user_id}&filter[is_completed]=0&limit=250&page={page}",
        )
        if st == 204 or not (200 <= st < 300):
            break
        chunk = (body.get("_embedded") or {}).get("tasks") or []
        items.extend(task for task in chunk if not task.get("is_completed"))
        if len(chunk) < 250:
            break
        page += 1
    return items


def _patch_task(amo: lib.Amo, task_id: int, user_id: int) -> int:
    st, _ = amo.req("PATCH", f"/api/v4/tasks/{task_id}", {"responsible_user_id": user_id})
    return st


def _patch_lead(amo: lib.Amo, lead_id: int, user_id: int) -> int:
    st, _ = amo.req("PATCH", f"/api/v4/leads/{lead_id}", {"responsible_user_id": user_id})
    return st


def move_tasks_on_lead(amo: lib.Amo, lead_id: int, off_id: int, on_id: int, apply: bool) -> int:
    st, body = amo.req(
        "GET",
        f"/api/v4/tasks?filter[entity_id]={lead_id}&filter[is_completed]=0&limit=50",
    )
    if not (200 <= st < 300):
        return 0
    n = 0
    for task in (body.get("_embedded") or {}).get("tasks") or []:
        if task.get("is_completed") or task.get("responsible_user_id") != off_id:
            continue
        n += 1
        if apply:
            code = _patch_task(amo, task["id"], on_id)
            print(f"  task {task['id']} [{code}] -> {on_id}", flush=True)
    return n


def nudge_lead(amo: lib.Amo, lead: dict) -> int | None:
    """Если сделка продаж на той, кто сегодня не работает, отдаёт дежурной."""
    on_id, _why = shift_roster.duty()
    off_id = shift_roster.other(on_id)
    if not on_id or not off_id:
        return None
    if lead.get("pipeline_id") not in SALES:
        return None
    if lead.get("status_id") in CLOSED:
        return None
    if lead.get("responsible_user_id") != off_id:
        return None
    code = _patch_lead(amo, lead["id"], on_id)
    print(f"  lead {lead['id']} [{code}] {off_id} -> {on_id}", flush=True)
    if 200 <= code < 300:
        move_tasks_on_lead(amo, lead["id"], off_id, on_id, apply=True)
        return on_id
    return None


def run(apply: bool, pretend: int | None = None) -> str:
    if pretend:
        on_id = pretend
        name = "Кристина" if on_id == lib.USER_KRISTINA else "Таня" if on_id == lib.USER_TANYA else str(on_id)
        why = f"просмотр, как будто работает {name}"
    else:
        on_id, why = shift_roster.duty()
    print(f"График: {why}", flush=True)
    off_id = shift_roster.other(on_id)
    if not on_id or not off_id:
        print("Сегодня никого не переключаю", flush=True)
        return "skip"
    amo = amo_client()
    tasks = open_tasks(amo, off_id)
    lead_ids: set[int] = set()
    print(f"Открытых задач у {off_id}: {len(tasks)}", flush=True)
    for task in tasks:
        text = (task.get("text") or "").replace("\n", " ")[:80]
        print(f"  задача {task['id']} {text}", flush=True)
        if apply:
            code = _patch_task(amo, task["id"], on_id)
            print(f"    -> {on_id} [{code}]", flush=True)
        if task.get("entity_type") == "leads" and task.get("entity_id"):
            lead_ids.add(int(task["entity_id"]))
    moved_leads = 0
    for lead_id in sorted(lead_ids):
        st, lead = amo.req("GET", f"/api/v4/leads/{lead_id}")
        if not (200 <= st < 300) or not isinstance(lead, dict):
            continue
        if lead.get("pipeline_id") not in SALES or lead.get("status_id") in CLOSED:
            continue
        if lead.get("responsible_user_id") != off_id:
            continue
        moved_leads += 1
        print(f"  сделка {lead_id} вместе с задачей", flush=True)
        if apply:
            code = _patch_lead(amo, lead_id, on_id)
            print(f"    -> {on_id} [{code}]", flush=True)
    print(
        f"{'Перенесено' if apply else 'План'}: задач {len(tasks)}, сделок рядом с ними {moved_leads}",
        flush=True,
    )
    return "ok"


def maybe_morning() -> float:
    """После 7:00 по Москве. Повтор, если галочку сменили."""
    now = datetime.now(TZ)
    if now.hour < MORNING_HOUR:
        nxt = now.replace(hour=MORNING_HOUR, minute=0, second=5, microsecond=0)
        return nxt.timestamp()
    try:
        added = shift_roster.ensure_horizon()
        if added:
            print(f"График продлён на {added} дн.", flush=True)
    except Exception as exc:
        print(f"График не продлился: {exc}", flush=True)
    on_id, why = shift_roster.duty()
    if not on_id:
        mark = f"{now.date().isoformat()} skip {why}"
        path = stamp_path()
        if not path.is_file() or path.read_text(encoding="utf-8").strip() != mark:
            print(f"Утро: {why}", flush=True)
            path.write_text(mark, encoding="utf-8")
        return time.time() + 900
    stamp = f"{now.date().isoformat()} {on_id}"
    path = stamp_path()
    if path.is_file() and path.read_text(encoding="utf-8").strip() == stamp:
        return time.time() + 1800
    print(f"Утро, смена {why}", flush=True)
    result = run(apply=True)
    if result == "ok":
        path.write_text(stamp, encoding="utf-8")
    return time.time() + 1800


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Собрать пустой лист с галочками")
    parser.add_argument("--preview", type=int, default=0, help="Чей id считать дежурным, без записи")
    args = parser.parse_args()
    if args.publish:
        n = shift_roster.publish()
        print(f"Лист готов, дней {n}")
        return
    run(apply=args.apply, pretend=args.preview or None)


if __name__ == "__main__":
    main()
