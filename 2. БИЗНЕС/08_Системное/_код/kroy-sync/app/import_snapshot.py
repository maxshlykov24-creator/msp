"""Catalog import only. Stock snapshot remains observation, never overwrites our ledger."""
import argparse
import hashlib
import json
from pathlib import Path
from .db import Entity, Item, engine_for, transaction


def import_catalog(engine, data):
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        if s.get(Entity, 'snapshot:' + digest):
            return {'already': True}
        added = 0
        for row in data['items']:
            if s.get(Item, row['id']) is None:
                s.add(Item(**row))
                added += 1
        summary = {k: data[k] for k in ('source', 'started_at', 'finished_at', 'location', 'atomic_snapshot')}
        summary.update({'catalog_count': len(data['items']), 'stock_rows': len(data['balances']),
                        'status': 'observed_not_posted', 'sha256': digest})
        s.add(Entity(id='snapshot:' + digest, kind='snapshot', version=1, data=summary))
        s.add(Entity(id='observation:' + digest, kind='observation', version=1,
                     data={'balances': data['balances'], 'location': data['location']}))
    return {'added': added, **summary}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('path')
    args = p.parse_args()
    print(json.dumps(import_catalog(engine_for(), json.loads(Path(args.path).read_text())), ensure_ascii=False))
