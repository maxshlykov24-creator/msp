#!/usr/bin/env python3
"""Репетиция на боевой базе: прогнать все группы дублей через хаб и показать вердикты.

Зачем: выдержка этапа по времени ловит ошибки только на живом трафике, а его
мало. Репетиция даёт ту же проверку за минуты — на реальных карточках, в shadow
без изменений в Kommo (на боевых этапах прогон применяет решения по-настоящему).

Запуск (скрипт не входит в образ, поэтому подаётся на stdin):

    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - sweep" < scripts/rehearsal.py
    …подождать 2-3 минуты…
    ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && \\
        docker compose exec -T worker python - report" < scripts/rehearsal.py
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict

from sqlalchemy import text

from app.db import session_scope
from app.identity import contact_phones
from app.kommo.client import KommoClient
from app.queue import enqueue

_LAST_RUN = """
with last_run as (
    select max(split_part(event_key, ':', 2)) as run from inbox where event_key like 'rehearsal:%'
), per_group as (
    select e.id, e.phone,
           coalesce(string_agg(distinct d.action, ', ') filter (
               where d.action in ('contact_merge.skip', 'contact_merge.resolved',
                                  'manual_merge_tag', 'deal_dedup.skip',
                                  'deal_dedup.resolved', 'cross_funnel.folded')
           ), 'НЕТ РЕШЕНИЯ') as verdict,
           count(*) filter (where d.action = 'event.done') as done
    from inbox e
    join last_run on e.event_key like 'rehearsal:' || last_run.run || ':%'
    left join decisions d on d.inbox_id = e.id
    group by e.id, e.phone
)
select verdict, count(*), string_agg(phone, ' ' order by phone) from per_group group by 1 order by 2 desc
"""


def sweep() -> None:
    """Найти все группы дублей по телефону и поставить их в очередь обработки."""
    client = KommoClient()
    phone_map: dict[str, list[int]] = defaultdict(list)
    for c in client.paginate("/contacts", "contacts", params={"limit": 250}):
        for phone in contact_phones(c):
            phone_map[phone].append(int(c["id"]))
    groups = {p: sorted(set(ids)) for p, ids in phone_map.items() if len(set(ids)) > 1}

    run = int(time.time())
    queued = 0
    with session_scope() as s:
        for phone, ids in groups.items():
            _, created = enqueue(s, source="kommo", event_type="update_contact",
                                 event_key=f"rehearsal:{run}:{phone}",
                                 payload={"entity_id": min(ids), "reason": "rehearsal"},
                                 phone=phone)
            queued += int(created)
    print(f"групп дублей: {len(groups)}, поставлено в очередь: {queued}")
    print("через 2-3 минуты: тот же скрипт с аргументом report")


def report() -> None:
    """Вердикты последнего прогона: сколько склеек, сколько в ручную очередь, есть ли пропуски."""
    with session_scope() as s:
        rows = list(s.execute(text(_LAST_RUN)))
    if not rows:
        print("прогонов не было")
        return
    for verdict, count, phones in rows:
        print(f"{count:4}  {verdict}")
        if verdict == "НЕТ РЕШЕНИЯ":
            print(f"      номера: {phones}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"sweep": sweep, "report": report}.get(cmd, report)()
