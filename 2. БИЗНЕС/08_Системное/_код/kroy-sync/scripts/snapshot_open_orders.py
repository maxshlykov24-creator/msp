import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, '/opt/kroy-sync')
import config
sys.path.insert(0, '/opt/kroy-app-stage')
from app.connectors import ReadClient, ozon_unfulfilled, wb_new_orders, wb_order_statuses

now = datetime.now(timezone.utc)
start, finish = (now - timedelta(days=90)).isoformat(), (now + timedelta(days=30)).isoformat()
ozon = ReadClient('ozon', {'Api-Key': os.environ['OZON_API_KEY'], 'Client-Id': os.environ['OZON_CLIENT_ID']})
wb = ReadClient('wb', {'Authorization': os.environ['WB_TOKEN']})
previous = json.loads(Path('/opt/kroy-app-stage/marketplaces-snapshot.json').read_text())
new = wb_new_orders(wb)
ids = list(dict.fromkeys([o['id'] for o in previous['wb_orders']] + [o['id'] for o in new]))
data = {'started_at': now.isoformat(), 'mode': 'observation_only', 'cutoff_from': start, 'cutoff_to': finish,
        'ozon_unfulfilled': ozon_unfulfilled(ozon, start, finish),
        'wb_new': new, 'wb_tracked_statuses': wb_order_statuses(wb, ids),
        'limits': 'Ozon: окно срока передачи -90/+30 дней; WB: новые и ранее наблюдавшиеся ID, не весь исторический список'}
data['finished_at'] = datetime.now(timezone.utc).isoformat()
print(json.dumps(data, ensure_ascii=False))
