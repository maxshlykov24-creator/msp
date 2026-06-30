"""
Дозаливает пропущенные позиции в заказы покупателей нового аккаунта.
Алгоритм:
  1. Для каждого заказа из uuid_map["customerorder"] сравниваем позиции old vs new.
  2. Если в старом есть позиции, которых нет в новом — добавляем через
     POST /entity/customerorder/{new_uuid}/positions
"""
import requests, json, time, sys
from pathlib import Path

OLD_TOKEN = 'b047463b41ff7d77010fbad1002240fb9d959ebe'
NEW_TOKEN  = '619a40e860cb6ad975c3d05cae7157b29caccc0e'
BASE       = 'https://api.moysklad.ru/api/remap/1.2'
UMAP_PATH  = Path(__file__).parent / 'uuid_map.json'
DRY_RUN    = '--dry' in sys.argv  # python fix_missing_positions.py --dry


def make_session(token):
    s = requests.Session()
    s.headers.update({'Authorization': f'Bearer {token}', 'Accept-Encoding': 'gzip'})
    return s


def req_get(s, url, params=None, retries=6):
    for attempt in range(retries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def req_post(s, url, body, retries=6):
    for attempt in range(retries):
        try:
            r = s.post(url, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def meta(href, t):
    return {'meta': {'href': href, 'type': t, 'mediaType': 'application/json'}}


def uid_of(pos):
    return pos['assortment']['meta']['href'].split('/')[-1].split('?')[0]


def main():
    old = make_session(OLD_TOKEN)
    new = make_session(NEW_TOKEN)
    umap = json.loads(UMAP_PATH.read_text())
    order_map = umap.get('customerorder', {})
    prod_map  = umap.get('product', {})
    svc_map   = umap.get('service', {})
    var_map   = umap.get('variant', {})
    uom_map   = umap.get('uom', {})
    all_maps  = {**prod_map, **svc_map, **var_map}

    orders_checked = orders_fixed = positions_added = positions_failed = 0

    print(f'DRY_RUN={DRY_RUN}  |  customerorder в uuid_map: {len(order_map)}', flush=True)

    pairs = list(order_map.items())
    for i, (old_uuid, new_uuid) in enumerate(pairs):
        if i % 100 == 0:
            print(f'  {i}/{len(pairs)}  fixed={orders_fixed} pos_added={positions_added}', flush=True)

        # Позиции старого
        try:
            old_pos = req_get(old, f'{BASE}/entity/customerorder/{old_uuid}/positions',
                              params={'limit': 1000, 'expand': 'assortment'}).get('rows', [])
            time.sleep(0.1)
        except Exception as e:
            print(f'  ✗ get old positions {old_uuid[:8]}: {e}')
            continue

        # Позиции нового
        try:
            new_pos = req_get(new, f'{BASE}/entity/customerorder/{new_uuid}/positions',
                              params={'limit': 1000}).get('rows', [])
            time.sleep(0.1)
        except Exception as e:
            print(f'  ✗ get new positions {new_uuid[:8]}: {e}')
            continue

        orders_checked += 1
        new_uid_set = {uid_of(p) for p in new_pos}

        # Ищем пропущенные
        to_add = []
        for pos in old_pos:
            a = pos.get('assortment', {})
            a_href = a.get('meta', {}).get('href', '')
            old_a_uid = a_href.split('/')[-1].split('?')[0]
            old_a_type = a_href.split('/entity/')[-1].split('/')[0] if '/entity/' in a_href else 'product'

            new_a_uid = all_maps.get(old_a_uid)
            if not new_a_uid:
                continue  # нет маппинга — пропускаем
            if new_a_uid in new_uid_set:
                continue  # уже есть

            # Маппим единицу измерения
            uom_href = (pos.get('uom') or {}).get('meta', {}).get('href', '')
            old_uom_uid = uom_href.split('/')[-1].split('?')[0] if uom_href else ''
            new_uom_uid = uom_map.get(old_uom_uid)

            pos_body = {
                'assortment': meta(f'{BASE}/entity/{old_a_type}/{new_a_uid}', old_a_type),
                'quantity': pos.get('quantity', 1),
                'price': pos.get('price', 0),
                'discount': pos.get('discount', 0),
                'vat': pos.get('vat', 0),
            }
            if new_uom_uid:
                pos_body['uom'] = meta(f'{BASE}/entity/uom/{new_uom_uid}', 'uom')
            if pos.get('reserve') is not None:
                pos_body['reserve'] = pos['reserve']
            to_add.append(pos_body)

        if not to_add:
            continue

        orders_fixed += 1
        if DRY_RUN:
            print(f'  DRY {new_uuid[:8]}: +{len(to_add)} позиций')
            positions_added += len(to_add)
            continue

        # POST пачкой
        try:
            req_post(new, f'{BASE}/entity/customerorder/{new_uuid}/positions', to_add)
            positions_added += len(to_add)
            time.sleep(0.1)
        except Exception as e:
            print(f'  ✗ POST positions {new_uuid[:8]}: {e}')
            positions_failed += len(to_add)

    print(f'\n=== Итог ===')
    print(f'  Заказов проверено:   {orders_checked}')
    print(f'  С доп. позициями:    {orders_fixed}')
    print(f'  Позиций добавлено:   {positions_added}')
    print(f'  Позиций с ошибкой:   {positions_failed}')


if __name__ == '__main__':
    main()
