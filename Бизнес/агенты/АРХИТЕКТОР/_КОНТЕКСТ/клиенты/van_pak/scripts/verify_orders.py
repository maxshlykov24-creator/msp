"""
Верификация: все ли заказы покупателей 2026 перенесены,
и совпадают ли позиции (товары/услуги) в каждом заказе.
"""
import requests, json, time, random
from pathlib import Path
from collections import defaultdict

from ms_common import OLD_TOKEN, NEW_TOKEN  # noqa: E402 — unified token source
BASE = 'https://api.moysklad.ru/api/remap/1.2'
UMAP_PATH = Path(__file__).parent / 'uuid_map.json'

def make_session(token):
    s = requests.Session()
    s.headers.update({'Authorization': f'Bearer {token}', 'Accept-Encoding': 'gzip'})
    return s

def req(s, url, params=None, retries=5):
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

def fetch_all(s, entity, extra_params=None):
    params = {'limit': 100, 'offset': 0, **(extra_params or {})}
    result = []
    while True:
        data = req(s, f'{BASE}/entity/{entity}', params)
        rows = data.get('rows', [])
        result.extend(rows)
        if len(rows) < 100:
            break
        params = {**params, 'offset': params['offset'] + 100}
        time.sleep(0.15)
    return result


def main():
    old = make_session(OLD_TOKEN)
    new = make_session(NEW_TOKEN)
    umap = json.loads(UMAP_PATH.read_text())
    order_map = umap.get('customerorder', {})
    prod_map = umap.get('product', {})
    svc_map = umap.get('service', {})
    var_map = umap.get('variant', {})
    all_assort_map = {**prod_map, **svc_map, **var_map}

    # ── 1. Загружаем все заказы 2026 ──────────────────────────────────────
    print('Загружаю заказы 2026 из СТАРОГО...', flush=True)
    old_orders = fetch_all(old, 'customerorder',
                           {'filter': 'moment>2026-01-01 00:00:00'})
    old_by_uuid = {o['meta']['href'].split('/')[-1]: o for o in old_orders}
    print(f'  Старый: {len(old_orders)}')

    print('Загружаю заказы 2026 из НОВОГО...', flush=True)
    new_orders = fetch_all(new, 'customerorder',
                           {'filter': 'moment>2026-01-01 00:00:00'})
    new_by_uuid = {o['meta']['href'].split('/')[-1]: o for o in new_orders}
    print(f'  Новый: {len(new_orders)}')

    # ── 2. Какие заказы отсутствуют в новом ──────────────────────────────
    missing = []
    for old_uuid, o in old_by_uuid.items():
        new_uuid = order_map.get(old_uuid)
        if not new_uuid or new_uuid not in new_by_uuid:
            missing.append({'name': o.get('name'), 'moment': o.get('moment'),
                            'old_uuid': old_uuid, 'new_uuid': new_uuid})

    print(f'\nОтсутствует в новом: {len(missing)}')
    for m in missing:
        print(f'  {m["name"]}  moment={m["moment"]}  new_uuid={m["new_uuid"]}')

    # ── 3. Полная проверка позиций ─────────────────────────────────────────
    ok = 0
    mismatch_list = []
    not_in_umap = 0

    # только заказы, которые есть в обоих аккаунтах
    paired = [(old_uuid, order_map[old_uuid])
              for old_uuid in old_by_uuid
              if old_uuid in order_map and order_map[old_uuid] in new_by_uuid]

    total = len(paired)
    print(f'\nПроверяю позиции во всех {total} перенесённых заказах...')

    for i, (old_uuid, new_uuid) in enumerate(paired):
        if i % 50 == 0:
            print(f'  {i}/{total}...', flush=True)

        old_pos = req(old, f'{BASE}/entity/customerorder/{old_uuid}/positions',
                      params={'limit': 1000}).get('rows', [])
        time.sleep(0.12)
        new_pos = req(new, f'{BASE}/entity/customerorder/{new_uuid}/positions',
                      params={'limit': 1000}).get('rows', [])
        time.sleep(0.12)

        def uid(pos):
            return pos['assortment']['meta']['href'].split('/')[-1].split('?')[0]

        # строим множество (new_uuid_assortment, quantity)
        old_set = set()
        unmapped_items = []
        for pos in old_pos:
            old_a = uid(pos)
            new_a = all_assort_map.get(old_a, old_a)  # если нет маппинга — оставляем старый
            if old_a not in all_assort_map:
                unmapped_items.append(old_a)
            old_set.add((new_a, round(pos.get('quantity', 0))))

        new_set = {(uid(pos), round(pos.get('quantity', 0))) for pos in new_pos}

        if old_set == new_set:
            ok += 1
        else:
            extra = new_set - old_set
            missing_pos = old_set - new_set
            mismatch_list.append({
                'name': old_by_uuid[old_uuid].get('name'),
                'old_count': len(old_pos),
                'new_count': len(new_pos),
                'missing_pos': list(missing_pos)[:5],
                'extra_pos': list(extra)[:5],
                'unmapped': unmapped_items,
            })

    print(f'\n=== Результат проверки позиций ({total} заказов) ===')
    print(f'  Полностью совпадают: {ok}')
    print(f'  Расхождения:         {len(mismatch_list)}')
    if mismatch_list:
        print('\n  Топ расхождений:')
        for m in mismatch_list[:20]:
            unmapped_info = f'  unmapped_old={m["unmapped"][:2]}' if m['unmapped'] else ''
            print(f'  {m["name"]}: old={m["old_count"]} new={m["new_count"]} '
                  f'missing={len(m["missing_pos"])} extra={len(m["extra_pos"])}{unmapped_info}')

    # ── 4. Сохранить отчёт ────────────────────────────────────────────────
    report = {
        'old_total': len(old_orders),
        'new_total': len(new_orders),
        'missing_from_new': missing,
        'paired': total,
        'ok': ok,
        'mismatches': mismatch_list,
    }
    out = Path(__file__).parent / 'verify_orders_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'\nОтчёт сохранён: {out}')


if __name__ == '__main__':
    main()
