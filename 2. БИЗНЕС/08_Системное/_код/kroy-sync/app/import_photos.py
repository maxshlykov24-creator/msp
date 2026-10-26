"""Attach WB photos only when the garment barcode maps to exactly one WB card."""
import hashlib
import json
from urllib.parse import urlparse
from sqlalchemy import select
from .db import Entity, Item, engine_for, transaction


def import_photos(engine, data):
    by_code = {}
    for card in data['cards']:
        for size in card['sizes']:
            for code in size['skus']:
                by_code.setdefault(str(code), {})[str(card['id'])] = card
    matched, conflicts = 0, 0
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        if s.get(Entity, 'photos:' + digest):
            return {'already': True}
        for item in s.scalars(select(Item).where(Item.kind == 'garment')):
            matches = {}
            for code in item.barcodes:
                matches.update(by_code.get(code, {}))
            if len(matches) > 1:
                conflicts += 1
                continue
            if len(matches) == 1:
                card = next(iter(matches.values()))
                photo = next((p.get('c516x688') or p.get('big') for p in card['photos'] if p.get('c516x688') or p.get('big')), '')
                host = urlparse(photo).hostname or ''
                if not photo.startswith('https://') or not (host.endswith('.wbbasket.ru') or host.endswith('.wbstatic.net')):
                    continue
                item.photo = photo
                item.source = {**item.source, 'photo_source': 'wb', 'photo_card': card['id'], 'photo_at': data['at']}
                matched += 1
        s.add(Entity(id='photos:' + digest, kind='photo_import', version=1,
                     data={'at': data['at'], 'matched': matched, 'conflicts': conflicts, 'sha256': digest}))
    return {'matched': matched, 'conflicts': conflicts}


if __name__ == '__main__':
    import sys
    from pathlib import Path
    print(json.dumps(import_photos(engine_for(), json.loads(Path(sys.argv[1]).read_text()))))
