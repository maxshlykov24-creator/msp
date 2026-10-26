"""Observation adapters. Explicit allowlist cannot mutate marketplace state."""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

READS = {
    'wb': {'/api/v3/warehouses': 'GET', '/api/v3/orders/new': 'GET', '/api/v3/orders/status': 'POST'},
    'ozon': {'/v2/warehouse/list': 'POST', '/v4/posting/fbs/list': 'POST', '/v4/product/info/stocks': 'POST'},
}
HOSTS = {'wb': 'https://marketplace-api.wildberries.ru', 'ozon': 'https://api-seller.ozon.ru'}


class ReadFailure(RuntimeError):
    pass


class ReadClient:
    def __init__(self, platform, headers):
        self.platform, self.headers = platform, headers

    def request(self, path, body=None):
        method = READS[self.platform].get(path)
        if not method:
            raise ReadFailure('Endpoint is not on the observation allowlist')
        request = urllib.request.Request(HOSTS[self.platform] + path,
                 data=json.dumps(body or {}).encode() if method == 'POST' else None,
                 headers={**self.headers, 'Content-Type': 'application/json'}, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise ReadFailure('Read failed: HTTP ' + str(e.code)) from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise ReadFailure('Read failed: transport error') from None
            time.sleep(2 ** attempt)
        raise ReadFailure('Read exhausted')


def ozon_postings(client, since, until):
    rows, seen, cursor = [], set(), ''
    for _ in range(1000):
        result = client.request('/v4/posting/fbs/list', {
            'dir': 'ASC', 'filter': {'since': since, 'to': until},
            'limit': 100, 'cursor': cursor,
            'with': {'analytics_data': False, 'financial_data': False}})
        result = result.get('result', result)
        if not isinstance(result.get('postings'), list) or type(result.get('has_next')) is not bool:
            raise ReadFailure('Postings response incomplete')
        for posting in result['postings']:
            key = posting.get('posting_number')
            if not key or key in seen:
                raise ReadFailure('Missing or repeated posting; snapshot is not complete')
            seen.add(key)
            # No recipients, contacts, addresses, prices or personal information.
            rows.append({k: posting.get(k) for k in ('posting_number', 'status', 'in_process_at', 'shipment_date')})
            rows[-1]['products'] = [{k: product.get(k) for k in ('sku', 'offer_id', 'quantity')} for product in posting.get('products', [])]
        if not result['has_next']:
            return rows
        next_cursor = result.get('cursor')
        if not next_cursor or next_cursor == cursor or not result['postings']:
            raise ReadFailure('Cursor did not advance')
        cursor = next_cursor
    raise ReadFailure('Pagination cap reached')


def ozon_stocks(client):
    rows, seen, cursor = [], set(), ''
    for _ in range(1000):
        result = client.request('/v4/product/info/stocks', {'filter': {'visibility': 'ALL'}, 'limit': 1000, 'cursor': cursor})
        result = result.get('result', result)
        batch = result.get('items')
        if not isinstance(batch, list):
            raise ReadFailure('Stock rows missing')
        for item in batch:
            key = item.get('product_id') or item.get('offer_id')
            if not key or key in seen:
                raise ReadFailure('Repeated or missing stock identity')
            seen.add(key)
            rows.append({k: item.get(k) for k in ('product_id', 'offer_id', 'stocks')})
        total = result.get('total_items', result.get('total'))
        if total is not None and len(rows) == int(total):
            return rows
        if len(batch) < 1000:
            if total is not None and len(rows) != int(total):
                raise ReadFailure('Stock total mismatch')
            return rows
        next_cursor = result.get('cursor')
        if not next_cursor or next_cursor == cursor:
            raise ReadFailure('Stock cursor did not advance')
        cursor = next_cursor
    raise ReadFailure('Pagination cap reached')


def wb_new_orders(client):
    data = client.request('/api/v3/orders/new')
    if not isinstance(data.get('orders'), list):
        raise ReadFailure('WB orders response incomplete')
    # New orders only. Explicitly not history or all open orders.
    return [{k: o.get(k) for k in ('id', 'createdAt', 'warehouseId', 'nmId', 'chrtId', 'skus')} for o in data['orders']]
