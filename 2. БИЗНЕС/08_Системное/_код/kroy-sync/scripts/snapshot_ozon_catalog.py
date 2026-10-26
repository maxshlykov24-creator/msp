"""Read product identity/barcodes only. No changes and no customer information."""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, '/opt/kroy-sync')
import config

stock = json.loads(Path('/opt/kroy-app-stage/marketplaces-snapshot.json').read_text())['ozon_stocks']
ids = [str(row['product_id']) for row in stock]
items = []
for start in range(0, len(ids), 1000):
    req = urllib.request.Request('https://api-seller.ozon.ru/v3/product/info/list',
        data=json.dumps({'product_id': ids[start:start + 1000]}).encode(), method='POST',
        headers={'Client-Id': os.environ['OZON_CLIENT_ID'], 'Api-Key': os.environ['OZON_API_KEY'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit('Catalog read HTTP ' + str(exc.code)) from None
    batch = result.get('items')
    if not isinstance(batch, list):
        raise SystemExit('Catalog response missing items')
    for row in batch:
        items.append({k: row.get(k) for k in ('id', 'offer_id', 'barcodes', 'sources', 'is_archived')})
if {str(i['id']) for i in items} != set(ids) or len(items) != len(ids):
    raise SystemExit('Catalog identities do not match requested stock identities')
print(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'items': items, 'scope': 'all_product_ids_in_stock_snapshot'}))
