"""Store the sanitized open-order observation separately from warehouse orders."""
import json
import hashlib
import os
from collections import Counter
from pathlib import Path
import sys
ROOT = Path('/opt/kroy-app-stage')
sys.path.insert(0, str(ROOT))
for line in (ROOT / '.env.app').read_text().splitlines():
    key, value = line.split('=', 1)
    os.environ[key] = value
from app.db import Entity, engine_for, transaction

data = json.loads((ROOT / 'open-orders.json').read_text())
digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
dates = [r['in_process_at'] for r in data['ozon_unfulfilled'] if r.get('in_process_at')]
summary = {'at': data['finished_at'], 'ozon_count': len(data['ozon_unfulfilled']),
           'ozon_statuses': dict(Counter(r['status'] for r in data['ozon_unfulfilled'])),
           'earliest_creation': min(dates) if dates else None, 'wb_tracked': len(data['wb_tracked_statuses']),
           'limits': data['limits'], 'sha256': digest, 'mode': 'observation_only'}
with transaction(engine_for()) as s:
    if not s.get(Entity, 'open-order-source:' + digest):
        s.add(Entity(id='open-order-source:' + digest, kind='open_order_source', version=1, data=data))
        s.add(Entity(id='open-order-summary:' + digest, kind='open_order_summary', version=1, data=summary))
print(json.dumps(summary, ensure_ascii=False))
