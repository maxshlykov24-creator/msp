"""Read-only capability probe. Never exports raw orders or credentials."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error
sys.path.insert(0, '/opt/kroy-sync')
import config


def request(url, headers, body=None):
    req = urllib.request.Request(url, headers={**headers, 'Content-Type': 'application/json'},
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get('message', '')
        except Exception:
            detail = ''
        return {'validation_message': detail[:400]}, e.code
    except Exception:
        return None, 'transport_error'


def main():
    now = datetime.now(timezone.utc)
    wb = {'Authorization': os.environ.get('WB_TOKEN', '')}
    ozon = {'Api-Key': os.environ.get('OZON_API_KEY', ''), 'Client-Id': os.environ.get('OZON_CLIENT_ID', '')}
    checks = [
        ('wb.warehouses', 'https://marketplace-api.wildberries.ru/api/v3/warehouses', wb, None),
        ('wb.orders_new', 'https://marketplace-api.wildberries.ru/api/v3/orders/new', wb, None),
        ('ozon.warehouses', 'https://api-seller.ozon.ru/v2/warehouse/list', ozon, {'limit': 100}),
        ('ozon.fbs', 'https://api-seller.ozon.ru/v4/posting/fbs/list', ozon,
         {'dir': 'ASC', 'filter': {'since': (now-timedelta(days=14)).isoformat(), 'to': now.isoformat()},
          'limit': 100, 'offset': 0, 'with': {'analytics_data': False, 'financial_data': False}}),
        ('ozon.stocks', 'https://api-seller.ozon.ru/v4/product/info/stocks', ozon,
         {'filter': {'visibility': 'ALL'}, 'limit': 1000}),
    ]
    output = []
    for name, url, headers, body in checks:
        data, status = request(url, headers, body)
        row = {'check': name, 'http': status, 'at': datetime.now(timezone.utc).isoformat()}
        if isinstance(data, list):
            row['rows'] = len(data)
        elif isinstance(data, dict):
            result = data.get('result', data)
            if isinstance(result, list):
                row['rows'] = len(result)
            elif isinstance(result, dict):
                row['fields'] = list(result)
                if 'validation_message' in result:
                    row['validation_message'] = result['validation_message']
                for k in ('orders', 'postings', 'items'):
                    if isinstance(result.get(k), list):
                        row['rows'] = len(result[k])
                row['has_next'] = result.get('has_next')
        output.append(row)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
