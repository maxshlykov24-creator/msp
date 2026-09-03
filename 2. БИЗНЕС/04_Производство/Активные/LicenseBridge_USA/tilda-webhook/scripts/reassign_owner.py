#!/usr/bin/env python3
"""Разовый перенос открытых сделок и незакрытых задач с одного менеджера на другого.

Нужен при увольнении: пока карточки и задачи висят на уволенном, живой менеджер
их не видит в «своих», а задачи копятся просроченными (19.08.2026: Илона →
Александра, 312 открытых сделок и 160 задач, из них 136 просрочены).

Правила:
- закрытые сделки не трогаем: по ним считается история и наследование
  ответственного в app/assignment.py;
- воронки задаём явно, по умолчанию только основная продажа;
- задачи переносим вместе со сделками, иначе «задача на уволенном» вернётся;
- перед записью кладём снимок «id → старый ответственный» в scripts/snapshots/,
  им же откатываемся: python3 scripts/reassign_owner.py --rollback <файл> --apply

По умолчанию сухой прогон:
    python3 scripts/reassign_owner.py --from 15537380 --to 15648532
    python3 scripts/reassign_owner.py --from 15537380 --to 15648532 --apply
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import CLOSED_STATUS_IDS, settings  # noqa: E402
from app.kommo.client import KommoClient  # noqa: E402

SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"
BATCH = 50


def status_names(client: KommoClient) -> dict[int, str]:
    out: dict[int, str] = {}
    for pl in client.pipelines():
        for st in ((pl.get("_embedded") or {}).get("statuses") or []):
            out[int(st["id"])] = st.get("name", "")
    return out


def open_leads(client: KommoClient, owner: int, pipelines: set[int]) -> list[dict[str, Any]]:
    out = []
    for lead in client.paginate("/leads", "leads",
                                params={"filter[responsible_user_id]": owner}):
        if int(lead.get("pipeline_id") or 0) not in pipelines:
            continue
        if int(lead.get("status_id") or 0) in CLOSED_STATUS_IDS or lead.get("closed_at"):
            continue
        out.append(lead)
    return out


def open_tasks(client: KommoClient, owner: int, lead_ids: set[int]) -> list[dict[str, Any]]:
    out = []
    for task in client.paginate("/tasks", "tasks",
                                params={"filter[responsible_user_id]": owner,
                                        "filter[is_completed]": 0}):
        if task.get("entity_type") == "leads" and int(task.get("entity_id") or 0) not in lead_ids:
            continue  # задача по закрытой сделке — вместе с ней и остаётся
        out.append(task)
    return out


def patch_batches(client: KommoClient, path: str, items: list[dict[str, Any]]) -> int:
    done = 0
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        if client.patch(path, chunk) is None:
            print(f"  !! {path} батч {i // BATCH + 1} не прошёл", file=sys.stderr)
            continue
        done += len(chunk)
        print(f"  {path}: {done}/{len(items)}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", type=int, help="id уволенного")
    ap.add_argument("--to", dest="dst", type=int, help="id того, кто принимает")
    ap.add_argument("--pipelines", default=str(settings.pipeline_id),
                    help="через запятую; по умолчанию основная воронка продаж")
    ap.add_argument("--skip-tasks", action="store_true")
    ap.add_argument("--rollback", help="файл снимка: вернуть прежних ответственных")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    client = KommoClient()

    if a.rollback:
        snap = json.loads(pathlib.Path(a.rollback).read_text(encoding="utf-8"))
        leads = [{"id": i, "responsible_user_id": o} for i, o in snap["leads"].items()]
        tasks = [{"id": i, "responsible_user_id": o} for i, o in snap["tasks"].items()]
        print(f"откат: сделок {len(leads)}, задач {len(tasks)}")
        if not a.apply:
            print("сухой прогон, для записи добавь --apply")
            return 0
        patch_batches(client, "/leads", leads)
        patch_batches(client, "/tasks", tasks)
        return 0

    if not a.src or not a.dst:
        ap.error("нужны --from и --to")

    pipelines = {int(x) for x in a.pipelines.split(",") if x.strip()}
    users = {int(u["id"]): u for u in client.users()}
    dst = users.get(a.dst)
    if not dst or not (dst.get("rights") or {}).get("is_active"):
        print(f"принимающий {a.dst} неактивен или не найден — Kommo такую запись отвергнет",
              file=sys.stderr)
        return 1

    names = status_names(client)
    leads = open_leads(client, a.src, pipelines)
    lead_ids = {int(x["id"]) for x in leads}
    tasks = [] if a.skip_tasks else open_tasks(client, a.src, lead_ids)

    by_status = Counter(names.get(int(x.get("status_id") or 0), "?") for x in leads)
    print(f"открытых сделок: {len(leads)}")
    for name, cnt in by_status.most_common():
        print(f"  {cnt:>4}  {name}")
    print(f"незакрытых задач: {len(tasks)}")

    if not leads and not tasks:
        print("переносить нечего")
        return 0

    if not a.apply:
        print("сухой прогон, для записи добавь --apply")
        return 0

    SNAPSHOTS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snap_path = SNAPSHOTS / f"reassign_{a.src}_to_{a.dst}_{stamp}.json"
    snap_path.write_text(json.dumps({
        "from": a.src, "to": a.dst,
        "leads": {str(x["id"]): int(x.get("responsible_user_id") or 0) for x in leads},
        "tasks": {str(x["id"]): int(x.get("responsible_user_id") or 0) for x in tasks},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"снимок для отката: {snap_path}")

    patch_batches(client, "/leads",
                  [{"id": int(x["id"]), "responsible_user_id": a.dst} for x in leads])
    if tasks:
        patch_batches(client, "/tasks",
                      [{"id": int(x["id"]), "responsible_user_id": a.dst} for x in tasks])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
