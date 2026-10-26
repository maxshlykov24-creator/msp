"""Isolated browser fixture using real catalog identities and explicit TEST quantities."""
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select
from uuid import uuid4
from app.db import Base, User, Item, engine_for, transaction
from app.import_snapshot import import_catalog
from app.service import execute
engine = engine_for()
Base.metadata.create_all(engine)
d = json.loads(Path('.runtime/ms-snapshot.json').read_text())
import_catalog(engine, d)
with transaction(engine) as s:
    user = User(id='e2e', name='Проверка интерфейса', role='owner', active=True,
                token_hash=hashlib.sha256(Path('.runtime/owner.key').read_bytes()).hexdigest())
    s.add(user)
    items = list(s.scalars(select(Item).where(Item.kind == 'garment').order_by(Item.model, Item.color, Item.size)))
    first = items[0]
    selected = [i for i in items if i.model == first.model and i.color == first.color][:2]
    material = s.scalar(select(Item).where(Item.kind == 'material'))
for i, q in [(material, 100)] + [(i, 0) for i in selected]:
    execute(engine, user, str(uuid4()), 'stock.adjust', {'item_id': i.id, 'location': d['location'], 'version': 0, 'physical': q}, 'ТЕСТ: условные количества в отдельной базе, не фактический остаток')
Path('.runtime/e2e-fixture.json').write_text(json.dumps({'model': first.model, 'items': [{'id': i.id, 'name': i.name} for i in selected],
                                                       'material': material.id, 'location': d['location']}, ensure_ascii=False))
print('E2E fixture prepared; source systems unchanged')
