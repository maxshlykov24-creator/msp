"""Short task numbers allocated inside the existing serialized write transaction."""
import re
from sqlalchemy import select
from .db import Entity, Document

COUNTER = 'sequence:cutting-task'


def next_task_number(s):
    counter = s.get(Entity, COUNTER)
    if counter is None:
        existing = [int(e.data['number'][3:]) for e in s.scalars(select(Entity).where(Entity.kind == 'task'))
                    if re.fullmatch(r'КР-\d{1,6}', e.data.get('number', ''))]
        counter = Entity(id=COUNTER, kind='sequence', version=1, data={'value': max(existing, default=0)})
        s.add(counter)
        s.flush()
    value = counter.data['value'] + 1
    counter.data = {'value': value}
    counter.version += 1
    return f'КР-{value:03d}'


def backfill_task_numbers(s):
    """Retain old references for search; do not change products, facts or quantities."""
    tasks = list(s.scalars(select(Entity).where(Entity.kind == 'task')))
    docs = {d.id: d.created_at.isoformat() for d in s.scalars(select(Document))}
    tasks.sort(key=lambda e: (e.data.get('created_at') or docs.get(e.id, ''), e.id))
    changed = 0
    for e in tasks:
        if re.fullmatch(r'КР-\d{1,6}', e.data.get('number', '')): continue
        data = dict(e.data)
        if data.get('number'): data['legacy_number'] = data['number']
        data['number'] = next_task_number(s)
        e.data = data
        e.version += 1
        changed += 1
    return changed
