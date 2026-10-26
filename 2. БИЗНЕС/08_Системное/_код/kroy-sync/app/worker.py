"""Expiry actions share the warehouse transaction and journal. No network writes."""
import copy
from datetime import datetime
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from .db import Document, Entity, Operation, engine_for, now, transaction
from .service import move, save


def tick(engine):
    created = 0
    with transaction(engine) as s:
        for e in s.scalars(select(Entity).where(Entity.kind == 'freeze')):
            d = copy.deepcopy(e.data)
            if not d.get('until') or Decimal(d['remaining']) <= 0 or datetime.fromisoformat(d['until']) > now():
                continue
            if d.get('mode') == 'auto':
                op = str(uuid5(NAMESPACE_URL, 'kroy:expiry:' + e.id + ':' + str(e.version)))
                s.add(Document(id=op, kind='freeze.auto_release', actor='system:expiry',
                               reason='Настроенное владельцем автоматическое снятие по сроку', payload={'freeze_id': e.id, 'version': e.version}))
                q = Decimal(d['remaining'])
                move(s, op, d['item_id'], d['location'], {'frozen': -q, 'free': q})
                d['remaining'] = '0'
                save(e, d)
                created += 1
            elif not d.get('reminded'):
                notice_id = 'expiry:' + e.id + ':' + str(e.version)
                s.add(Entity(id=notice_id, kind='notice', version=1,
                             data={'type': 'freeze_expired', 'freeze_id': e.id, 'item_id': d['item_id'],
                                   'message': 'Срок заморозки истёк. Снять или продлить вручную', 'at': now().isoformat()}))
                d['reminded'] = True
                save(e, d)
                created += 1
    return created


if __name__ == '__main__':
    import time
    engine = engine_for()
    while True:
        tick(engine)
        time.sleep(30)
