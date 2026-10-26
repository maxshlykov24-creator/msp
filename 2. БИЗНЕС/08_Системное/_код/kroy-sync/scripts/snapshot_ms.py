"""Run on existing host. GET only. Emit sanitized catalog/stock, no customers or credentials."""
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

sys.path.insert(0, os.environ.get('KROY_LEGACY_PATH', '/opt/kroy-sync'))
import ms_api


def rows(path):
    result, offset, expected = [], 0, None
    for _ in range(1000):
        sep = '&' if '?' in path else '?'
        data = ms_api.request('GET', path + sep + 'limit=100&offset=' + str(offset))
        if not isinstance(data.get('rows'), list):
            raise RuntimeError('Incomplete response; no snapshot emitted')
        batch = data['rows']
        size = data.get('meta', {}).get('size')
        if expected is None:
            expected = size
        elif size is not None and size != expected:
            raise RuntimeError('Collection changed during pagination; retry')
        result.extend(batch)
        if len(batch) < 100:
            if expected is not None and expected != len(result):
                raise RuntimeError('Pagination mismatch')
            return result
        offset += 100
    raise RuntimeError('Pagination cap reached')


def aid(row):
    return row.get('id') or row.get('meta', {}).get('href', '').split('?')[0].rstrip('/').split('/')[-1]


def main():
    started = datetime.now(timezone.utc).isoformat()
    products = [p for p in rows('/entity/product?expand=uom') if p.get('pathName', '').split('/')[0] in ('YUJI', 'Рулоны')]
    by_id = {p['id']: p for p in products}
    variants = rows('/entity/variant')
    items, variant_parents = [], set()
    for v in variants:
        pid = aid(v.get('product', {}))
        if pid not in by_id or by_id[pid].get('pathName') == 'Рулоны':
            continue
        p = by_id[pid]
        variant_parents.add(pid)
        name = p['name']
        parts = name.split(' — ', 1)
        items.append({'id': v['id'], 'name': v['name'], 'model': parts[0], 'color': parts[1] if len(parts) == 2 else '',
                      'size': next((c['value'] for c in v.get('characteristics', []) if c['name'] == 'Размер'), ''),
                      'category': p.get('pathName', ''), 'kind': 'garment', 'unit': 'piece',
                      'barcodes': [str(code) for b in v.get('barcodes', []) for code in b.values()],
                      'photo': '', 'source': {'system': 'moysklad', 'parent': pid}})
    for p in products:
        if p['id'] in variant_parents:
            continue
        material = p.get('pathName') == 'Рулоны'
        unit_name = (p.get('uom') or {}).get('name', '')
        if material and unit_name not in ('м', 'шт'):
            raise RuntimeError('Material unit needs explicit mapping')
        items.append({'id': p['id'], 'name': p['name'], 'model': p['name'], 'color': '', 'size': '',
                      'category': p.get('pathName', ''), 'kind': 'material' if material else 'garment',
                      'unit': 'meter' if material and unit_name == 'м' else 'piece',
                      'barcodes': [str(code) for b in p.get('barcodes', []) for code in b.values()],
                      'photo': '', 'source': {'system': 'moysklad'}})
    store_id = os.environ.get('MS_STORE_ID', '').strip()
    if not store_id:
        raise RuntimeError('No accounting store configured')
    stock = rows('/report/stock/all?filter=' + quote('store=' + ms_api.BASE + '/entity/store/' + store_id))
    allowed = {i['id'] for i in items}
    balances = [{'item_id': aid(r), 'location': store_id, 'stock': r.get('stock'), 'reserve': r.get('reserve'),
                 'quantity': r.get('quantity')} for r in stock if aid(r) in allowed]
    print(json.dumps({'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                      'source': 'moysklad', 'mode': 'read-only-observation', 'atomic_snapshot': False,
                      'location': store_id, 'items': items, 'balances': balances,
                      'missing_stock_is_unknown': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
