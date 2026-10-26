"""Save read-only observations; mappings are candidates, never production approval."""
import hashlib
import json
from sqlalchemy import select
from .db import Entity, Item, engine_for, transaction


def import_observation(engine, data):
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        if s.get(Entity, 'marketplace_snapshot:' + digest):
            return {'already': True}
        barcodes = {}
        for item in s.scalars(select(Item)):
            for barcode in item.barcodes:
                barcodes.setdefault(barcode, []).append(item.id)
        wb_matched = 0
        for order in data['wb_orders']:
            candidates = {item for barcode in order.get('skus', []) for item in barcodes.get(barcode, [])}
            if len(candidates) == 1:
                wb_matched += 1
        summary = {'at': data['finished_at'], 'wb_new_orders': len(data['wb_orders']),
                   'ozon_postings': len(data['ozon_postings']), 'ozon_stock_rows': len(data['ozon_stocks']),
                   'since': data['since'], 'until': data['until'], 'wb_unique_barcode_candidates': wb_matched,
                   'mode': 'observation_only', 'complete_for_query': True,
                   'limits': 'WB: только новые; Ozon: период 14 дней, не вся история открытых заказов',
                   'sha256': digest}
        s.add(Entity(id='marketplace_snapshot:' + digest, kind='marketplace_snapshot', version=1, data=summary))
        s.add(Entity(id='marketplace_observation:' + digest, kind='marketplace_observation', version=1, data=data))
    return summary


if __name__ == '__main__':
    import sys
    from pathlib import Path
    print(json.dumps(import_observation(engine_for(), json.loads(Path(sys.argv[1]).read_text())), ensure_ascii=False))
