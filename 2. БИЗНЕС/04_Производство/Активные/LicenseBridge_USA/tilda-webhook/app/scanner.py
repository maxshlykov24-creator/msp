"""Scanner: (1) плановый детект дублей по телефону — страховка на пропущенные
вебхуки; (2) еженедельный авто-возврат тегов пула (mc_*/md_*).

Сам scanner не удаляет/не сливает карточки: найденные группы кладёт в inbox
(их разбирает worker по общим правилам), а авто-возврат тегов только снимает
СВОИ теги с разрешённых пар и освобождает слот.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from app.actions import Ctx, current_tags, log_decision
from app.config import settings
from app.db import session_scope
from app.identity import contact_phones
from app.kommo.client import KommoClient
from app.merge_tags import free_slot, pool_stats
from app.models import DupManual
from app.queue import enqueue
from app.rollout import current_flags

log = logging.getLogger("scanner")


# ── 1. Детект дублей по телефону ──
def detect_duplicates(client: KommoClient) -> dict[str, int]:
    phone_map: dict[str, list[int]] = defaultdict(list)
    scanned = 0
    for c in client.paginate("/contacts", "contacts", params={"limit": 250}):
        scanned += 1
        if scanned > settings.scanner_max_contacts:
            break
        for phone in contact_phones(c):
            phone_map[phone].append(int(c["id"]))

    groups = {p: ids for p, ids in phone_map.items() if len(set(ids)) > 1}
    queued = 0
    if settings.scanner_autoqueue:
        run_ts = int(time.time())
        with session_scope() as s:
            for phone, ids in groups.items():
                oldest = min(ids)
                _, created = enqueue(
                    s, source="kommo", event_type="update_contact",
                    event_key=f"scanner:{run_ts // 3600}:{phone}",
                    payload={"entity_id": oldest, "reason": "scanner_dup"},
                    phone=phone,
                )
                if created:
                    queued += 1
    log.info("scanner: scanned=%s dup_groups=%s queued=%s", scanned, len(groups), queued)
    return {"scanned": scanned, "dup_groups": len(groups), "queued": queued}


# ── 2. Авто-возврат тегов пула ──
def _entity_has_tag(client: KommoClient, kind: str, entity_id: int, tag: str) -> bool:
    try:
        if kind == "contact":
            e = client.get_contact(entity_id, with_="leads")
        else:
            e = client.get_lead(entity_id, with_="contacts")
    except Exception:
        return True  # не смогли проверить — считаем, что ещё висит (не трогаем)
    if not e:
        return False  # сущность удалена/склеена → тег на ней не считается
    return tag in set(current_tags(e))


def reclaim_tags(client: KommoClient) -> dict[str, int]:
    """Пара на <2 сущностях → тег «пустой»: снять маркер+тег с оставшейся,
    освободить слот, закрыть DupManual."""
    reclaimed = 0
    with session_scope() as s:
        open_pairs = s.query(DupManual).filter(DupManual.status == "open").all()
        flags = current_flags(s)
        ctx = Ctx(client=client, session=s, inbox_id=None, phone=None,
                  shadow=flags.shadow, flags=flags)
        for pair in open_pairs:
            kind = pair.kind
            still: list[int] = [eid for eid in pair.entity_ids
                                if _entity_has_tag(client, kind, int(eid), pair.tag)]
            if len(still) >= 2:
                continue
            marker = settings.tag_marker_contact if kind == "contact" else settings.tag_marker_deal
            for eid in still:
                if ctx.shadow:
                    log_decision(ctx, "reclaim.strip.shadow", kind=kind, id=eid, tag=pair.tag)
                    continue
                entity = (client.get_contact(int(eid), with_="leads") if kind == "contact"
                          else client.get_lead(int(eid), with_="contacts"))
                keep = sorted(t for t in current_tags(entity or {})
                              if t not in (pair.tag, marker))
                kommo_entity = "contacts" if kind == "contact" else "leads"
                client.set_tags(kommo_entity, int(eid), keep)
            free_slot(s, kind, pair.tag)
            pair.status = "resolved"
            from datetime import datetime, timezone
            pair.resolved_at = datetime.now(timezone.utc)
            reclaimed += 1
        stats = pool_stats(s)
    log.info("reclaim: pairs_reclaimed=%s pool=%s", reclaimed, stats)
    return {"reclaimed": reclaimed, "pool": stats}


def handle_scanner_event(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """На случай синтетических событий source=scanner (сейчас scanner кладёт
    события как source=kommo/update_contact, но оставляем совместимость)."""
    from app.worker import handle_kommo_update_contact

    cid = int(payload.get("entity_id") or 0)
    if cid:
        return handle_kommo_update_contact(ctx, cid)
    return {"action": "scanner_noop"}


def run_scheduler() -> None:
    """Запускается внутри worker-процесса (APScheduler)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    client = KommoClient()
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(lambda: detect_duplicates(client), "interval",
                  minutes=settings.scanner_interval_min, id="detect", max_instances=1)
    sched.add_job(lambda: reclaim_tags(client), "interval",
                  hours=settings.tag_reclaim_interval_hours, id="reclaim", max_instances=1)
    sched.start()
    log.info("scanner scheduler started detect=%smin reclaim=%sh",
             settings.scanner_interval_min, settings.tag_reclaim_interval_hours)
