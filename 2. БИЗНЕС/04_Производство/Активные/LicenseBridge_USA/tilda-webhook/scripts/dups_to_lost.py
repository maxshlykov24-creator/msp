#!/usr/bin/env python3
"""Разовый разбор УЖЕ накопившихся дубль-сделок: лишние уводим в «Провал».

Логика повторяет app/dedup_deals: карточки одного номера, которые по имени
сводятся к одному человеку (транслитерация/опечатки — не разные люди), считаются
одним клиентом. Внутри одной воронки остаётся сделка, которая дальше по этапам;
при равных этапах — более старая. Остальные → «Провал» + причина «дубль» + тег.

Ничего не удаляет. По умолчанию сухой прогон:
    python3 scripts/dups_to_lost.py            # только отчёт
    python3 scripts/dups_to_lost.py --apply    # выполнить
"""
from __future__ import annotations

import argparse
import csv
import datetime
import pathlib
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import CLOSED_STATUS_IDS, settings  # noqa: E402
from app.identity import contact_phones, same_person_name  # noqa: E402
from app.kommo.client import KommoClient  # noqa: E402

WORK_PIPELINES = (settings.pipeline_id, settings.assembly_pipeline_id)
DUP_TAGS = {settings.tag_dup_deal, settings.tag_dup_to_delete}


def status_order(client: KommoClient) -> dict[int, tuple[int, str]]:
    out: dict[int, tuple[int, str]] = {}
    for pl in client.pipelines():
        for st in ((pl.get("_embedded") or {}).get("statuses") or []):
            out[int(st["id"])] = (int(st.get("sort") or 0), st.get("name", ""))
    return out


def load_contacts(client: KommoClient, max_contacts: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in client.paginate("/contacts", "contacts", params={"limit": 250, "with": "leads"}):
        out.append(c)
        if len(out) >= max_contacts:
            break
    return out


def person_clusters(contacts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Разбить карточки одного номера на группы «один человек» по имени."""
    clusters: list[list[dict[str, Any]]] = []
    for c in sorted(contacts, key=lambda x: x.get("created_at") or 0):
        for cl in clusters:
            if all(same_person_name(c.get("name"), other.get("name")) for other in cl):
                cl.append(c)
                break
        else:
            clusters.append([c])
    return clusters


def tags_of(entity: dict[str, Any]) -> set[str]:
    return {t.get("name", "") for t in ((entity.get("_embedded") or {}).get("tags") or [])}


def is_open(lead: dict[str, Any]) -> bool:
    return (lead.get("closed_at") in (None, 0)
            and int(lead.get("status_id") or 0) not in CLOSED_STATUS_IDS)


def load_open_leads(client: KommoClient) -> dict[int, list[dict[str, Any]]]:
    """Открытые сделки рабочих воронок, разложенные по id контакта.

    Одним проходом по /leads: по одной сделке через API это десятки минут на
    боевой базе."""
    by_contact: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for l in client.paginate("/leads", "leads", params={"limit": 250, "with": "contacts"}):
        if int(l.get("pipeline_id") or 0) not in WORK_PIPELINES or not is_open(l):
            continue
        # Сделки, уже помеченные хабом как дубль, НЕ пропускаем: они и есть
        # накопившиеся дубли, которые до сих пор висят в рабочих этапах.
        if settings.handoff_tag in tags_of(l):
            continue
        for c in ((l.get("_embedded") or {}).get("contacts") or []):
            if c.get("id"):
                by_contact[int(c["id"])].append(l)
    return by_contact


def collect_actions(contacts: list[dict[str, Any]],
                    leads_by_contact: dict[int, list[dict[str, Any]]],
                    order: dict[int, tuple[int, str]],
                    orphans: list[dict[str, Any]] | None = None,
                    ) -> list[dict[str, Any]]:
    by_phone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in contacts:
        for phone in contact_phones(c):
            by_phone[phone].append(c)

    actions: list[dict[str, Any]] = []
    orphans = orphans if orphans is not None else []
    seen_pairs: set[tuple[int, int]] = set()
    for phone, group in sorted(by_phone.items()):
        uniq = {int(c["id"]): c for c in group}
        for cluster in person_clusters(list(uniq.values())):
            open_work = list({int(l["id"]): l
                              for c in cluster
                              for l in leads_by_contact.get(int(c["id"]), [])}.values())
            per_funnel: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for l in open_work:
                per_funnel[int(l["pipeline_id"])].append(l)
            for pipeline_id, funnel_leads in per_funnel.items():
                # уже помеченные хабом дубли главной быть не могут — уходят в конец
                ranked = sorted(funnel_leads,
                                key=lambda l: (bool(DUP_TAGS & tags_of(l)),
                                               -order.get(int(l.get("status_id") or 0),
                                                          (0, ""))[0],
                                               l.get("created_at") or 0))
                # Если открытая сделка одна и она помечена дублём — закрывать нельзя:
                # у клиента не останется ни одной живой (боевой случай «Сергей»,
                # #28821802: парную сделку кто-то уже закрыл руками). Такие — в
                # отчёт на ручную проверку.
                if len(funnel_leads) < 2 or DUP_TAGS & tags_of(ranked[0]):
                    for l in funnel_leads:
                        if DUP_TAGS & tags_of(l):
                            orphans.append({"phone": phone, "lead": int(l["id"]),
                                            "stage": order.get(int(l.get("status_id") or 0),
                                                               (0, "?"))[1]})
                    continue
                keeper, dups = ranked[0], ranked[1:]
                for dup in dups:
                    key = (int(keeper["id"]), int(dup["id"]))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    actions.append({
                        "phone": phone,
                        "pipeline_id": pipeline_id,
                        "contacts": sorted(int(c["id"]) for c in cluster),
                        "names": " | ".join(sorted({(c.get("name") or "—") for c in cluster})),
                        "keep_lead": int(keeper["id"]),
                        "keep_stage": order.get(int(keeper.get("status_id") or 0),
                                                (0, "?"))[1],
                        "keep_created": int(keeper.get("created_at") or 0),
                        "lost_lead": int(dup["id"]),
                        "lost_stage": order.get(int(dup.get("status_id") or 0), (0, "?"))[1],
                        "lost_created": int(dup.get("created_at") or 0),
                        "lost_name": dup.get("name") or "",
                    })
    return actions


def apply_action(client: KommoClient, act: dict[str, Any]) -> None:
    lead = client.get_lead(act["lost_lead"], with_="contacts")
    if not lead or not is_open(lead):
        return
    # `дубль_удалить` со сделки снимаем: удалять её больше не нужно, она в «Провале»
    tags = sorted((tags_of(lead) | {settings.tag_dup_deal}) - {settings.tag_dup_to_delete})
    client.update_lead(act["lost_lead"], {
        "status_id": settings.status_lost,
        "loss_reason_id": settings.dup_lost_reason_id,
        "_embedded": {"tags": [{"name": t} for t in tags]},
    })
    keep = act["keep_lead"]
    client.add_note("leads", act["lost_lead"],
                    f"Закрыт как дубль (разбор накопленных дублей)."
                    + (f" Работа ведётся в сделке #{keep}." if keep else ""))
    if keep:
        client.add_note("leads", keep,
                        f"Дубль #{act['lost_lead']} закрыт как «дубль» (разбор накопленных).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="выполнить, а не только показать")
    ap.add_argument("--max-contacts", type=int, default=settings.scanner_max_contacts)
    ap.add_argument("--limit", type=int, default=0, help="обработать не больше N дублей")
    ap.add_argument("--csv", default="", help="куда сохранить отчёт")
    args = ap.parse_args()

    client = KommoClient()
    try:
        order = status_order(client)
        contacts = load_contacts(client, args.max_contacts)
        leads_by_contact = load_open_leads(client)
        orphans: list[dict[str, Any]] = []
        actions = collect_actions(contacts, leads_by_contact, order, orphans)
        if args.limit:
            actions = actions[: args.limit]

        print(f"контактов просмотрено: {len(contacts)}; "
              f"карточек с открытыми сделками: {len(leads_by_contact)}")
        print(f"дубль-сделок к закрытию: {len(actions)}")
        for a in actions:
            lost_dt = datetime.datetime.fromtimestamp(a["lost_created"]).strftime("%Y-%m-%d")
            if a["keep_lead"]:
                keep_dt = datetime.datetime.fromtimestamp(a["keep_created"]).strftime("%Y-%m-%d")
                keep = f"оставляем #{a['keep_lead']} ({a['keep_stage']}, {keep_dt})"
            else:
                keep = "основной сделки нет (все открытые помечены дублями)"
            print(f"  {a['phone']} [{a['names']}] {keep} → "
                  f"в Провал #{a['lost_lead']} ({a['lost_stage']}, {lost_dt})")

        if orphans:
            print(f"\nпомечены дублём, но других открытых сделок нет — руками "
                  f"({len(orphans)}):")
            for o in orphans:
                print(f"  {o['phone']} #{o['lead']} ({o['stage']})")

        if args.csv and actions:
            path = pathlib.Path(args.csv)
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(actions[0].keys()))
                w.writeheader()
                w.writerows(actions)
            print(f"отчёт: {path}")

        if not args.apply:
            print("сухой прогон, ничего не изменено (--apply чтобы выполнить)")
            return 0

        done = 0
        for a in actions:
            try:
                apply_action(client, a)
                done += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ошибка на #{a['lost_lead']}: {exc}", file=sys.stderr)
        print(f"закрыто как дубль: {done} / {len(actions)}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
