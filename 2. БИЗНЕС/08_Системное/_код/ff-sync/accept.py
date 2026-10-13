"""Полная сверка учёта по проведённым Приёмкам и Отгрузкам МойСклад.

run() — актуальный полный источник. legacy_run() сохраняет старую проверку
для совместимости архивных тестов; в рабочем расписании она не вызывается.

Заведение карточки и заказ поставщика — ещё не приёмка. Товар считается
принятым, когда кладовщик провёл в МойСклад «Приёмку» по этому заказу и
остаток сел на склад. До этого партия ничего не должна: в lots.accepted_at
пусто, хранение и сборка по ней равны нулю.
"""

from datetime import datetime, timedelta, timezone

from db import init_db, lots_awaiting_accept, mark_lot_accepted, run_lock
from net import MS_BASE, ms_headers, req

MSK = timezone(timedelta(hours=3))
POS_LIMIT = 1000


def product_id_of(position):
    href = (((position.get("assortment") or {}).get("meta") or {}).get("href") or "")
    return href.rstrip("/").rsplit("/", 1)[-1].split("?")[0] if href else ""


def supply_positions(supply_id):
    """Позиции приёмки постранично: у большой поставки их больше сотни."""
    out = []
    offset = 0
    while True:
        r = req(
            "GET",
            MS_BASE + "/entity/supply/%s/positions" % supply_id,
            headers=ms_headers(),
            params={"limit": POS_LIMIT, "offset": offset},
        )
        if r.status_code != 200:
            return out, "позиции приёмки %s: %s" % (supply_id, r.status_code)
        data = r.json() or {}
        rows = data.get("rows") or []
        out.extend(rows)
        offset += len(rows)
        if len(rows) < POS_LIMIT or offset >= int((data.get("meta") or {}).get("size") or 0):
            return out, ""


def supplies_of_order(order_id):
    """Приёмки, привязанные к заказу поставщика."""
    r = req("GET", MS_BASE + "/entity/purchaseorder/%s" % order_id, headers=ms_headers())
    if r.status_code != 200:
        return [], "заказ поставщика %s: %s" % (order_id, r.status_code)
    ids = []
    for item in (r.json() or {}).get("supplies") or []:
        href = ((item or {}).get("meta") or {}).get("href") or ""
        sid = href.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if sid:
            ids.append(sid)
    return ids, ""


def accepted_map(order_id):
    """{ms_product_id: дата приёмки} по одному заказу поставщика.

    Берём самую раннюю проведённую приёмку: клиент платит с первых суток,
    когда товар физически лёг на склад, а не с последней долитой партии.
    """
    ids, err = supplies_of_order(order_id)
    if err:
        return {}, err
    found = {}
    for sid in ids:
        r = req("GET", MS_BASE + "/entity/supply/%s" % sid, headers=ms_headers())
        if r.status_code != 200:
            continue
        doc = r.json() or {}
        if not doc.get("applicable"):
            # черновик приёмки склад не двигает
            continue
        if doc.get("deleted"):
            # приёмка лежит в корзине: остаток она уже не держит
            continue
        when = str(doc.get("moment") or "").replace(" ", "T")
        if not when:
            continue
        rows, perr = supply_positions(sid)
        if perr:
            return found, perr
        for pos in rows:
            pid = product_id_of(pos)
            if not pid:
                continue
            if pid not in found or when < found[pid]:
                found[pid] = when
    return found, ""


def legacy_run(blocking=True):
    init_db()
    with run_lock("accept", blocking=blocking):
        return _run()


def _run():
    lots = lots_awaiting_accept()
    if not lots:
        return {"checked": 0, "accepted": [], "errors": []}
    cache = {}
    accepted = []
    errors = []
    for lot in lots:
        order_id = lot["ms_supply_id"]
        if order_id not in cache:
            cache[order_id], err = accepted_map(order_id)
            if err:
                errors.append(err)
        when = cache[order_id].get(lot["ms_product_id"])
        if not when:
            continue
        mark_lot_accepted(lot["id"], when)
        accepted.append({"id": lot["id"], "article": lot["article"] or "", "at": when[:16]})
        print("приёмка партии %s: %s" % (lot["id"], when[:16]))
    return {"checked": len(lots), "accepted": accepted, "errors": errors}


def stamp():
    return datetime.now(MSK).isoformat(timespec="seconds")



# Авторитетный учёт по полным проведённым документам МойСклад.
import hashlib
import json
import math
from collections import defaultdict
from db import connect, get_setting, set_setting
from ms import attrs_by_name, org_id, store_id


def ref_id(value):
    return product_id_of({'assortment': value})


def ms_pages(path, params=None):
    rows, seen, expected = [], set(), None
    while True:
        r = req('GET', MS_BASE + path, headers=ms_headers(),
                params=dict(params or {}, limit=1000, offset=len(rows)))
        if r.status_code != 200:
            raise ValueError('МойСклад: %s, HTTP %s. Учёт не изменён.' % (path, r.status_code))
        data = r.json()
        size, page = data.get('meta', {}).get('size'), data.get('rows', [])
        if size is None or (expected is not None and size != expected) or (not page and len(rows) < size):
            raise ValueError('Неполная выборка МойСклад: ' + path)
        expected = size
        for row in page:
            key = row.get('id') or row.get('meta', {}).get('href')
            if not key or key in seen:
                raise ValueError('Повтор страницы МойСклад: ' + path)
            seen.add(key)
        rows.extend(page)
        if len(rows) == size:
            return rows
        if len(rows) > size:
            raise ValueError('Размер выборки МойСклад изменился: ' + path)


def ms_get(path):
    r = req('GET', MS_BASE + path, headers=ms_headers())
    if r.status_code != 200:
        raise ValueError('МойСклад: %s, HTTP %s' % (path, r.status_code))
    return r.json()


def number(raw, field, optional=False):
    if raw is None or raw == '':
        if optional:
            return None
        raise ValueError('Не заполнено поле ' + field)
    try:
        value = float(str(raw).replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError('Некорректное поле ' + field)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Некорректное поле ' + field)
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def document_header(doc, kind):
    return {'id': doc['id'], 'kind': kind, 'name': doc.get('name', ''),
            'moment': doc.get('moment', ''), 'updated': doc.get('updated', ''),
            'posted': bool(doc.get('applicable') and not doc.get('deleted')),
            'store': ref_id(doc.get('store')), 'agent': ref_id(doc.get('agent')),
            'agent_type': doc.get('agent', {}).get('meta', {}).get('type', '')}


def fetch_snapshot():
    products, parties, party_cache = {}, {}, {}
    for archived in ('false', 'true'):
        for p in ms_pages('/entity/product', {'filter': 'archived=' + archived}):
            attrs = attrs_by_name(p)
            products[p['id']] = {'id': p['id'], 'name': p.get('name', ''), 'article': p.get('article', ''),
                'barcode': next((str(v) for row in p.get('barcodes', []) for k, v in row.items() if k != 'meta'), ''),
                'gtin': str(attrs.get('GTIN') or ''), 'tracking_type': p.get('trackingType', 'NOT_TRACKED'),
                'liters': number(attrs.get('Литраж, л'), 'Литраж, л', True),
                'pick_rate': number(attrs.get('Стоимость сборки, ₽'), 'Стоимость сборки, ₽', True),
                'archived': bool(p.get('archived')), 'updated': p.get('updated', '')}
    # Не пропускаем другие документы, которые могли изменить остаток.
    for kind in ('salesreturn', 'purchasereturn', 'enter', 'loss', 'move', 'retaildemand', 'retailsalesreturn'):
        if any(x.get('applicable') and not x.get('deleted') for x in ms_pages('/entity/' + kind)):
            raise ValueError('Обнаружены движения %s. Нужна сверка их влияния на хранение; предыдущий учёт сохранён.' % kind)
    docs, headers = [], []
    known = {x['ms_counterparty_id'] for x in __import__('db').list_clients()}
    for kind in ('supply', 'demand'):
        originals = ms_pages('/entity/' + kind)
        for doc in originals:
            header = document_header(doc, kind)
            headers.append(header)
            if not header['posted']:
                continue
            moment = header['moment'].replace(' ', 'T')
            try:
                when = datetime.fromisoformat(moment).replace(tzinfo=MSK)
            except ValueError:
                raise ValueError('Некорректная дата документа %s №%s' % (kind, header['name']))
            if when > datetime.now(MSK):
                raise ValueError('Проведённый документ %s №%s имеет будущую дату. Проверь дату в МойСклад.' % (kind, header['name']))
            if not header['store']:
                raise ValueError('У документа не указан склад: ' + header['name'])
            cache_key = (header['agent_type'], header['agent'])
            if cache_key not in party_cache:
                if header['agent_type'] not in ('counterparty', 'organization') or not header['agent']:
                    raise ValueError('Не определён контрагент документа №' + header['name'])
                a = ms_get('/entity/%s/%s' % cache_key)
                if header['agent_type'] == 'organization':
                    if not a.get('inn'):
                        raise ValueError('У организации документа №%s не заполнен ИНН для привязки клиента' % header['name'])
                    candidates = []
                    for archived in ('false', 'true'):
                        candidates += ms_pages('/entity/counterparty', {'filter': 'inn=%s;archived=%s' % (a['inn'], archived)})
                    candidates = [x for x in candidates if x.get('inn') == a['inn']]
                    if a.get('kpp'):
                        candidates = [x for x in candidates if x.get('kpp') == a['kpp']]
                    selected = [x for x in candidates if x['id'] in known]
                    if len(selected) == 1:
                        a = selected[0]
                    elif len(candidates) == 1:
                        a = candidates[0]
                    else:
                        raise ValueError('Организация документа №%s не сопоставлена с одним контрагентом по реквизитам' % header['name'])
                parties[a['id']] = {'id': a['id'], 'name': a['name']}
                party_cache[cache_key] = a['id']
            positions = []
            for pos in ms_pages('/entity/%s/%s/positions' % (kind, doc['id'])):
                typ = pos.get('assortment', {}).get('meta', {}).get('type')
                if typ == 'service':
                    continue
                pid = product_id_of(pos)
                if typ != 'product' or pid not in products:
                    raise ValueError('Неподдержанная складская позиция в документе №%s: %s' % (header['name'], typ))
                positions.append({'id': pos['id'], 'product': pid, 'qty': number(pos.get('quantity'), 'количество')})
            docs.append(dict(header, moment=moment, party=party_cache[cache_key], positions=positions))
    # Документ мог измениться во время чтения его позиций.
    current = [document_header(d, kind) for kind in ('supply', 'demand') for d in ms_pages('/entity/' + kind)]
    if canonical(sorted(headers, key=lambda x: x['id'])) != canonical(sorted(current, key=lambda x: x['id'])):
        raise ValueError('Документы МойСклад изменились во время сверки. Повтори обновление.')
    # Тариф/литраж не должен меняться между первой страницей и расчётом.
    current_products = {p['id']: p.get('updated', '') for archived in ('false', 'true')
                        for p in ms_pages('/entity/product', {'filter': 'archived=' + archived})}
    if current_products != {i: p['updated'] for i, p in products.items()}:
        raise ValueError('Карточки товаров изменились во время сверки. Повтори обновление.')
    stock = {}
    for archived in ('false', 'true'):
        for r in ms_pages('/report/stock/all', {'filter': 'stockMode=all;archived=' + archived}):
            stock[ref_id(r)] = float(r.get('stock') or 0)
    return {'products': products, 'parties': parties, 'documents': docs, 'stock': stock}


def build_plan(snapshot, groups):
    products = snapshot['products']
    membership = {}
    for n, group in enumerate(groups):
        for party in group:
            if party in membership:
                raise ValueError('Контрагент указан в нескольких группах')
            membership[party] = 'group:%s' % n
    buckets, lots, moves, balance = defaultdict(list), {}, {}, defaultdict(float)
    for doc in sorted(snapshot['documents'], key=lambda d: (d['moment'], d['kind'] != 'supply', d['id'])):
        for pos in doc['positions']:
            qty, pid = pos['qty'], pos['product']
            if not qty:
                continue
            p = products[pid]
            key = 'ms:%s:%s:%s' % (doc['kind'], doc['id'], pos['id'])
            base = dict(ms_product_id=pid, article=p['article'], barcode=p.get('barcode', ''), gtin=p.get('gtin', ''),
                        name=p['name'], tracking_type=p.get('tracking_type', ''), liters=p.get('liters'),
                        pick_rate=p.get('pick_rate'), dims='', party=doc['party'], ms_store_id=doc['store'],
                        received_at=doc['moment'], accepted_at=doc['moment'], ms_supply_id=doc['id'],
                        source_key=key, source_document=('Приёмка' if doc['kind']=='supply' else 'Отгрузка')+' №'+doc['name'])
            bucket = (membership.get(doc['party'], doc['party']), doc['store'], pid)
            balance[pid] += qty if doc['kind']=='supply' else -qty
            if doc['kind'] == 'supply':
                lots[key] = dict(base, ledger_kind='receipt', qty_in=qty, qty_left=qty)
                buckets[bucket].append(key)
            else:
                # Самостоятельное начисление сборки контрагенту отгрузки.
                lots[key] = dict(base, ledger_kind='assembly', qty_in=0, qty_left=0)
                moves[key+':pick'] = {'lot_key': key, 'qty': qty, 'kind': 'pick', 'ref': doc['id'], 'created_at': doc['moment']}
                left = qty
                for receipt_key in buckets[bucket]:
                    receipt = lots[receipt_key]
                    take = min(left, receipt['qty_left'])
                    if take <= 0:
                        continue
                    receipt['qty_left'] -= take
                    moves[key+':'+receipt_key] = {'lot_key': receipt_key, 'qty': take, 'kind': 'ship', 'ref': doc['id'], 'created_at': doc['moment']}
                    left -= take
                    if left < 1e-7:
                        break
                if left > 1e-7:
                    raise ValueError('Отгрузка №%s: не хватает приёмки товара %s (%g). Проверь документы и склад.' % (doc['name'], p['article'] or pid, left))
    mismatches = [p for p in set(balance) | set(snapshot['stock']) if abs(balance[p]-snapshot['stock'].get(p,0)) > 1e-6]
    if mismatches:
        raise ValueError('Документы расходятся с остатками МойСклад по %s товарам. Учёт не изменён.' % len(mismatches))
    return {'lots': lots, 'moves': moves, 'qty': round(sum(x['qty_left'] for x in lots.values()), 3)}


def apply_plan(snapshot, plan, groups):
    """Атомарная замена активного представления; старые партии/счета сохраняются."""
    now = stamp()
    source = dict(snapshot, groups=groups)
    source_json = canonical(source)
    digest = hashlib.sha256(source_json.encode()).hexdigest()
    conn = connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            pending = conn.execute("SELECT value FROM settings WHERE key='billing:pending'").fetchone()
            if pending and json.loads(pending[0] or 'null'):
                raise ValueError('Есть неподтверждённый счёт. Сначала заверши его, затем обнови учёт.')
            old_lots = {(x['source_key'] or 'legacy:%s' % x['id']):dict(x) for x in conn.execute('SELECT * FROM lots')}
            old_moves = defaultdict(list)
            for m in conn.execute('SELECT m.*,l.source_key AS lot_key FROM lot_moves m JOIN lots l ON l.id=m.lot_id WHERE m.source_active=1'):
                old_moves[m['lot_key']].append(dict(m))
            cutoffs = {x['client_id']:x['day'] for x in conn.execute('SELECT client_id,max(billed_until) day FROM lots GROUP BY client_id') if x['day']}
            if conn.execute("SELECT ms_counterparty_id FROM clients WHERE coalesce(ms_counterparty_id,'')<>'' GROUP BY ms_counterparty_id HAVING count(*)>1").fetchone():
                raise ValueError('В панели есть дубли привязки к контрагенту МойСклад. Нужна сверка клиентов.')
            clients = {x['ms_counterparty_id']:x['id'] for x in conn.execute('SELECT * FROM clients')}
            for key, old in old_lots.items():
                closed = old['billed_until']
                if not closed:
                    continue
                new = plan['lots'].get(key)
                if not new or clients.get(new['party']) != old['client_id'] or any(new[k] != old[k] for k in ('ms_product_id','qty_in','accepted_at','ledger_kind')):
                    raise ValueError('Изменена уже выставленная партия %s. Нужна корректировка закрытого периода.' % old['id'])
                before = sorted((m['created_at'],m['kind'],m['qty']) for m in old_moves[key] if m['created_at'][:10] <= closed)
                after = sorted((m['created_at'],m['kind'],m['qty']) for m in plan['moves'].values() if m['lot_key']==key and m['created_at'][:10] <= closed)
                if before != after:
                    raise ValueError('Изменены движения закрытого периода партии %s. Нужна корректировка.' % old['id'])
            for key, new in plan['lots'].items():
                if key not in old_lots and new['party'] in clients and new['accepted_at'][:10] <= cutoffs.get(clients[new['party']], ''):
                    raise ValueError('Новый документ попал в уже выставленный период. Нужна сверка корректировки.')
            for pid, party in snapshot['parties'].items():
                if pid not in clients:
                    clients[pid] = conn.execute('INSERT INTO clients(code,name,ms_counterparty_id,ms_org_id,ms_store_id,active) VALUES (?,?,?,?,?,1)',
                        ('MS-'+pid.replace('-','')[:12], party['name'], pid, org_id(), store_id())).lastrowid
            conn.execute('UPDATE lots SET source_active=0')
            conn.execute('UPDATE lot_moves SET source_active=0')
            lot_ids = {}
            for key, values in plan['lots'].items():
                values = dict(values)
                values['client_id'] = clients[values.pop('party')]
                values['source_active'] = 1
                cols = list(values)
                old = old_lots.get(key)
                if old:
                    conn.execute('UPDATE lots SET '+','.join(k+'=?' for k in cols)+' WHERE id=?', [values[k] for k in cols]+[old['id']])
                    lot_ids[key] = old['id']
                else:
                    lot_ids[key] = conn.execute('INSERT INTO lots('+','.join(cols)+') VALUES ('+','.join('?' for k in cols)+')', [values[k] for k in cols]).lastrowid
            for key, move in plan['moves'].items():
                v = dict(move);v['lot_id'] = lot_ids[v.pop('lot_key')];v['source_key']=key;v['source_active']=1
                cols=list(v)
                conn.execute('INSERT INTO lot_moves('+','.join(cols)+') VALUES ('+','.join('?' for k in cols)+') ON CONFLICT(source_key) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in cols if k!='source_key'),[v[k] for k in cols])
            conn.execute('UPDATE ms_ledger_products SET active=0')
            conn.execute('UPDATE ms_ledger_documents SET active=0')
            for pid,p in snapshot['products'].items():
                conn.execute('INSERT INTO ms_ledger_products(id,data_json,synced_at,active) VALUES (?,?,?,1) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,synced_at=excluded.synced_at,active=1',(pid,canonical(p),now))
            for d in snapshot['documents']:
                conn.execute('INSERT INTO ms_ledger_documents(id,kind,data_json,synced_at,active) VALUES (?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,data_json=excluded.data_json,synced_at=excluded.synced_at,active=1',(d['id'],d['kind'],canonical(d),now))
            conn.execute('INSERT OR IGNORE INTO ms_ledger_history(digest,created_at,snapshot_json) VALUES (?,?,?)',(digest,now,source_json))
            summary = {'products':len(snapshot['products']), 'receipts':sum(d['kind']=='supply' for d in snapshot['documents']),
                       'demands':sum(d['kind']=='demand' for d in snapshot['documents']), 'lots':len(plan['lots']), 'qty':plan['qty']}
            for k,v in {'ledger:enabled':'1','ledger:last_success':now,'ledger:last_error':'','ledger:summary':canonical(summary)}.items():
                conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',(k,v))
            return dict(summary, checked=len(snapshot['documents']), accepted=[{'id':i} for i in lot_ids.values()], errors=[], last_sync=now)
    finally:
        conn.close()


def run(blocking=True):
    init_db()
    with run_lock('ledger', blocking=blocking):
        set_setting('ledger:last_attempt', stamp())
        try:
            groups = json.loads(get_setting('ledger:groups') or '[]')
            if not groups:
                raise ValueError('Не настроены подтверждённые группы контрагентов для учёта')
            snapshot = fetch_snapshot()
            plan = build_plan(snapshot, groups)
            with run_lock('billing'):
                return apply_plan(snapshot, plan, groups)
        except Exception as exc:
            set_setting('ledger:last_error', str(exc)[:500])
            raise


if __name__ == '__main__':
    result = run()
    print({k:v for k,v in result.items() if k!='accepted'})
