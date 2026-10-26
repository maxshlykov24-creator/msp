"""Read WB cards and photos; output only public product data, no account secrets."""
import json
import sys
import os
import urllib.request
from datetime import datetime, timezone
sys.path.insert(0, '/opt/kroy-sync')
import config
cards, seen, cursor = [], set(), {'limit': 100}
for _ in range(1000):
    req = urllib.request.Request('https://content-api.wildberries.ru/content/v2/get/cards/list',
        data=json.dumps({'settings': {'cursor': cursor, 'filter': {'withPhoto': -1}}}).encode(),
        headers={'Authorization': os.environ['WB_TOKEN'], 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=25) as response:
        data = json.load(response)
    if not isinstance(data.get('cards'), list):
        raise RuntimeError('Incomplete card response')
    for c in data['cards']:
        if c['nmID'] in seen:
            raise RuntimeError('Repeated card in pagination')
        seen.add(c['nmID'])
        cards.append({'id': c['nmID'], 'photos': c.get('photos', []),
                      'sizes': [{'chrtID': s.get('chrtID'), 'skus': s.get('skus', [])} for s in c.get('sizes', [])]})
    nxt = data.get('cursor', {})
    if len(data['cards']) < 100:
        break
    if not nxt.get('updatedAt') or not nxt.get('nmID'):
        raise RuntimeError('Missing cursor')
    cursor = {'limit': 100, 'updatedAt': nxt['updatedAt'], 'nmID': nxt['nmID']}
else:
    raise RuntimeError('Pagination cap reached')
print(json.dumps({'source': 'wb_catalog', 'at': datetime.now(timezone.utc).isoformat(), 'cards': cards}))
