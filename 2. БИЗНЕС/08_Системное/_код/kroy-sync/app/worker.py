"""Creates in-app reminders. External messages/publication require a verified adapter."""
import copy
from datetime import datetime
from sqlalchemy import select
from .db import Entity, engine_for, now, transaction
from .service import save


def tick(engine):
    created = 0
    with transaction(engine) as s:
        for e in s.scalars(select(Entity).where(Entity.kind == 'freeze')):
            d = copy.deepcopy(e.data)
            if d.get('until') and not d.get('reminded') and float(d['remaining']) > 0 and datetime.fromisoformat(d['until']) <= now():
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
