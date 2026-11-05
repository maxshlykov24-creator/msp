"""Own-ledger coverage and immutable cut calculation snapshots."""
import hashlib
import json
from datetime import datetime, timedelta, date
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Balance, Entity, Item, User, now
from .service import require
MSK = ZoneInfo('Europe/Moscow')


def parameters(raw):
    today = datetime.now(MSK).date()
    end = date.fromisoformat(raw.get('end') or str(today-timedelta(days=1)))
    start = date.fromisoformat(raw.get('start') or str(end-timedelta(days=13)))
    target = date.fromisoformat(raw.get('target_date') or str(today+timedelta(days=28)))
    horizon = int(raw.get('horizon', 28))
    require(start <= end < today and (end-start).days < 90, 'Период: до 90 полных дней, без сегодняшнего дня', 422)
    require(1 <= horizon <= 365 and target >= today, 'Проверь горизонт и дату покрытия', 422)
    return dict(start=str(start), end=str(end), horizon=horizon, target_date=str(target), location=raw['location'], include_uncertain=sorted(set(raw.get('include_uncertain', []))))


def calculate(s, raw):
    p = parameters(raw)
    ids = sorted(set(raw['item_ids']))
    require(ids and len(ids) <= 1000, 'Выбери товары', 422)
    today = str(datetime.now(MSK).date())
    coverage = {i: [] for i in ids}
    versions = []
    for e in s.scalars(select(Entity).where(Entity.kind.in_(['task', 'freeze']))):
        d = e.data
        if d.get('location') != p['location'] or e.id == raw.get('exclude_task'): continue
        due = d.get('available_on', '')
        candidates = d.get('remaining_products', []) if e.kind == 'task' and d.get('status') in ('approved', 'partial') else []
        if e.kind == 'freeze' and Decimal(d.get('remaining', '0')) > 0:
            candidates = [{'item_id': d['item_id'], 'quantity': d['remaining']}]
        touched = False
        for line in candidates:
            if line['item_id'] not in coverage: continue
            touched = True
            eligible = bool(due and today <= due <= p['target_date'])
            uncertain = not due or due < today
            selected = e.id in p['include_uncertain'] and uncertain
            coverage[line['item_id']].append(dict(id=e.id, kind=e.kind, title=d.get('title', 'Замороженный выпуск'), quantity=line['quantity'], date=due, included=eligible or selected, uncertain=uncertain, manual=selected))
        if touched: versions.append([e.id, e.version])
    windows = list(s.scalars(select(Entity).where(Entity.kind == 'sales_window')))
    mappings = {(e.data.get('platform'), e.data.get('item_id')): e.data for e in s.scalars(select(Entity).where(Entity.kind == 'mapping'))}
    status = s.get(Entity, 'cutting-sales-status')
    source_status = status.data.get('platforms', {}) if status else {}
    rows = []
    for iid in ids:
        item = s.get(Item, iid)
        require(item is not None and item.kind == 'garment', 'Неизвестное изделие', 422)
        b = s.scalar(select(Balance).where(Balance.item_id == iid, Balance.location == p['location']))
        platforms = {}
        for platform in ('wb', 'ozon'):
            candidates = [w for w in windows if w.data.get('platform') == platform and w.data['start'] <= p['start'] and w.data['end'] >= p['end'] and w.data.get('complete') and w.data.get('verified') and iid in w.data.get('covered_items', [])]
            w = max(candidates, key=lambda w: w.data['at'], default=None)
            sales = returns = 0
            if w:
                for event in w.data['events']:
                    if event['item_id'] == iid and p['start'] <= event['date'] <= p['end']:
                        if event['kind'] == 'sale': sales += event['quantity']
                        else: returns += event['quantity']
                versions.append([w.id, w.version])
            reason = None
            if not w:
                related = [e for e in windows if e.data.get('platform') == platform and e.data['start'] <= p['start'] and e.data['end'] >= p['end']]
                latest = max(related, key=lambda e:e.data['at'], default=None)
                mapping = mappings.get((platform, iid))
                if latest and iid in latest.data.get('blocked_items', {}): reason = latest.data['blocked_items'][iid]
                elif platform == 'wb' and not item.barcodes: reason = 'В карточке товара нет штрихкода для сверки с ВБ'
                elif mapping and mapping.get('state') != 'unique': reason = 'Нужно сопоставить товар с площадкой'
                else: reason = source_status.get(platform, {}).get('reason') or 'Нет полной сверенной выгрузки за выбранный период'
            platforms[platform] = dict(known=bool(w), sales=sales if w else None, returns=returns if w else None, at=w.data['at'] if w else None, net=sales-returns if w else None, reason=reason, source=w.data.get('source') if w else None, listed=iid not in w.data.get('not_listed_items',[]) if w else None, catalog_matches=w.data.get('catalog_matches',{}).get(iid,[]) if w else [])
        net = sum(v['sales']-v['returns'] for v in platforms.values()) if all(v['known'] for v in platforms.values()) else None
        future = sum((Decimal(c['quantity']) for c in coverage[iid] if c['included']), Decimal(0))
        stock_assumed_zero = not (b and b.known)
        free = b.free if not stock_assumed_zero else Decimal(0)
        target = int((Decimal(max(0, net))*p['horizon']/((date.fromisoformat(p['end'])-date.fromisoformat(p['start'])).days+1)).to_integral_value(rounding=ROUND_CEILING)) if net is not None else None
        rows.append(dict(item_id=iid, stock_assumed_zero=stock_assumed_zero, platforms=platforms, net=net, free=str(free) if free is not None else None, future=str(future), coverage=coverage[iid], target=target, recommendation=max(0, int(Decimal(target)-free-future)) if target is not None and free is not None else None, balance_version=b.version if b else None))
    body = dict(parameters=p, rows=rows, versions=sorted(versions))
    fingerprint_body = {**body, 'rows': [{**r, 'platforms': {k:{x:v for x,v in values.items() if x not in ('at','reason','source','catalog_matches')} for k,values in r['platforms'].items()}} for r in rows]}
    body['fingerprint'] = hashlib.sha256(json.dumps(fingerprint_body, sort_keys=True).encode()).hexdigest()
    body['calculated_at'] = now().isoformat()
    return body


def metadata(s, p):
    meta = dict(p.get('cutting') or {})
    require(isinstance(meta.get('comment', ''), str) and len(meta.get('comment', '')) <= 4000, 'Комментарий: не более 4000 символов', 422)
    require(meta.get('schema') == 1, 'Неверный формат задания', 422)
    for key in ('due_on', 'available_on'):
        if meta.get(key): date.fromisoformat(meta[key])
    if meta.get('due_on') and meta.get('available_on'):
        require(meta['available_on'] >= meta['due_on'], 'Доступность не может быть раньше кроя', 422)
    if meta.get('assignee'):
        u = s.get(User, meta['assignee'])
        require(u is not None and u.active and u.role in ('owner', 'cutter'), 'Исполнитель недоступен', 422)
    require(len(str(meta)) < 500000, 'Слишком большое задание', 422)
    return meta


def validate_issue(s, p, meta):
    require(meta.get('due_on') and meta.get('assignee'), 'Укажи срок кроя и исполнителя')
    require(meta.get('technology_confirmed') is True, 'Подтверди применимость ткани и раскладок')
    snapshot = meta.get('calculation')
    require(snapshot, 'Обнови расчёт перед выдачей')
    actual = calculate(s, {**snapshot['parameters'], 'item_ids': [r['item_id'] for r in snapshot['rows']], 'exclude_task': p.get('id')})
    require(snapshot['fingerprint'] == actual['fingerprint'], 'Остатки или ожидаемый выпуск изменились. Обнови расчёт и проверь разницу')
    included = {l['item_id'] for l in p['products']}
    require(included <= {r['item_id'] for r in actual['rows']}, 'В расчёте отсутствуют выбранные товары')
    for row in actual['rows']:
        if row['item_id'] in included and row['recommendation'] is None:
            require(meta.get('manual_reason', '').strip(), 'Для ручного количества без полных данных укажи основание')

    # Client-side validation is not a ledger boundary: validate every group server-side.
    from .service import quantity, get_item
    products, materials = {}, {}
    groups = meta.get('groups', [])
    require(groups, 'Нужны раскладки задания')
    for g in groups:
        require(g.get('ratio', '').strip(), 'Укажи соотношение размеров')
        material = get_item(s, g['material'])
        require(material.kind == 'material', 'Укажи материал')
        q = quantity(g['quantity'], material)
        materials[material.id] = materials.get(material.id, Decimal(0)) + q
        has_output = False
        for iid, raw in g['products'].items():
            q = quantity(raw, get_item(s, iid), positive=False)
            require(q >= 0 and get_item(s, iid).model == g['model'], 'Неверная модель или выпуск')
            if q:
                has_output = True
                products[iid] = products.get(iid, Decimal(0)) + q
        require(has_output, 'В каждом крое нужен выпуск')
    require(products == {l['item_id']: Decimal(str(l['quantity'])) for l in p['products']}, 'Раскладки не совпадают с выпуском')
    require(materials == {l['item_id']: Decimal(str(l['quantity'])) for l in p['materials']}, 'Раскладки не совпадают с расходом')
    for iid, actual in products.items():
        need = quantity(meta.get('quantities', {}).get(iid), get_item(s, iid))
        require(need * Decimal('.8') <= actual <= need * Decimal('1.2'), 'Выпуск за пределами допуска ±20%')
