"""Scanned-scope counting. Legacy scoped documents retain their original semantics."""
import copy
import re
from decimal import Decimal
from sqlalchemy import select
from .db import Entity, Document, Item, now
from .service import require, quantity, get_balance, get_item, entity, create_entity, save, move


def next_number(s):
    counter = s.get(Entity, 'sequence:count')
    if counter is None:
        used = [int(e.data['number'][3:]) for e in s.scalars(select(Entity).where(Entity.kind == 'count'))
                if re.fullmatch(r'ПР-\d+', e.data.get('number', ''))]
        counter = Entity(id='sequence:count', kind='sequence', version=1, data={'value': max(used, default=0)})
        s.add(counter)
        s.flush()
    value = counter.data['value'] + 1
    counter.data = {'value': value}
    counter.version += 1
    return f'ПР-{value:03d}'


def backfill_numbers(s):
    docs = {d.id: d.created_at.isoformat() for d in s.scalars(select(Document).where(Document.kind == 'count.create'))}
    rows = sorted(s.scalars(select(Entity).where(Entity.kind == 'count')), key=lambda e: (docs.get(e.id, ''), e.id))
    changed = 0
    for e in rows:
        if e.data.get('number'): continue
        save(e, {**e.data, 'number': next_number(s), 'created_at': docs.get(e.id)})
        changed += 1
    return changed


def snapshot(s, item_id, location):
    b = get_balance(s, item_id, location)
    return {'version': b.version, 'count': '0', 'expected': str(b.free + b.reserved) if b.known else None}


def scan(s, d, barcode):
    require(isinstance(barcode, str) and 0 < len(barcode.strip()) <= 300, 'Укажи штрихкод', 422)
    barcode = barcode.strip()
    from .product_identity import barcode_matches
    candidates = barcode_matches(s, barcode)
    if len(candidates) != 1 or (d.get('mode') != 'scanned' and candidates[0].id not in d['items']):
        require(len(d['unknown']) < 1000, 'Сначала разбери неизвестные коды', 422)
        d['unknown'].append({'barcode': barcode, 'reason': 'Код не найден или соответствует нескольким позициям'})
        return
    i = candidates[0]
    require(i.unit in ('piece', 'roll'), 'Метры требуют ручного измерения', 422)
    if i.id not in d['items']:
        require(len(d['items']) < 1000, 'Не более 1000 позиций в документе', 422)
        d['items'][i.id] = snapshot(s, i.id, d['location'])
    d['items'][i.id]['count'] = str(Decimal(d['items'][i.id]['count']) + 1)


def count_command(s, doc, command, p):
    if command == 'count.create':
        require(isinstance(p.get('location'), str) and p['location'].strip() and ':' not in p['location'], 'Нужен склад нашего учёта', 422)
        scanned = p.get('mode') == 'scanned'
        ids = p.get('item_ids', [])
        require(isinstance(ids, list) and (scanned or ids) and len(ids) <= 1000 and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids), 'Укажи уникальные позиции пересчёта', 422)
        d = {'location': p['location'], 'items': {i: snapshot(s, i, p['location']) for i in ids}, 'unknown': [], 'status': 'draft',
             'number': next_number(s), 'created_at': now().isoformat(), 'comment': ''}
        if scanned:
            require(not ids, 'Пересчёт по сканам начинается без выбранных позиций', 422)
            d['mode'] = 'scanned'
        if p.get('barcode') is not None: scan(s, d, p['barcode'])
        e = create_entity(s, doc, 'count', d)
        return {'id': e.id, 'version': 1}
    require(type(p.get('version')) is int, 'Нужна версия документа', 422)
    e = entity(s, p['id'], 'count', p['version'])
    d = copy.deepcopy(e.data)
    require(d['status'] == 'draft', 'Пересчёт уже проведён')
    if command == 'count.scan':
        scan(s, d, p.get('barcode'))
    elif command == 'count.set':
        require(p['item_id'] in d['items'], 'Позиция вне области пересчёта')
        q = quantity(p['quantity'], get_item(s, p['item_id']), positive=False)
        require(q >= 0, 'Посчитанное количество не может быть отрицательным')
        d['items'][p['item_id']]['count'] = str(q)
    elif command == 'count.resolve':
        index = p['index']
        require(type(index) is int and 0 <= index < len(d['unknown']), 'Неизвестная строка сканирования')
        row = d['unknown'][index]
        require(not row.get('resolved'), 'Скан уже разобран')
        if p.get('item_id'):
            i = get_item(s, p['item_id'])
            require(i.id in d['items'] or d.get('mode') == 'scanned', 'Позиция вне области пересчёта')
            require(i.unit in ('piece', 'roll'), 'Метры требуют измерения')
            if i.id not in d['items']:
                require(len(d['items']) < 1000, 'Не более 1000 позиций', 422)
                d['items'][i.id] = snapshot(s, i.id, d['location'])
            d['items'][i.id]['count'] = str(Decimal(d['items'][i.id]['count']) + 1)
        row.update(resolved=True, resolution=p.get('item_id') or 'ignored_out_of_scope', document_id=doc)
    elif command in ('count.save', 'count.refresh'):
        if 'comment' in p:
            require(isinstance(p['comment'], str) and len(p['comment']) <= 4000, 'Комментарий до 4000 символов', 422)
            d['comment'] = p['comment'].strip()
        if command == 'count.refresh':
            require(d.get('mode') == 'scanned', 'Старый пересчёт нужно повторить заново')
            for item_id, row in d['items'].items():
                updated = snapshot(s, item_id, d['location'])
                row.update(version=updated['version'], expected=updated['expected'])
        d['saved_at'] = now().isoformat()
    elif command == 'count.post':
        require(d['items'], 'Нет посчитанных позиций', 422)
        require(not any(not r.get('resolved') for r in d['unknown']), 'Есть неразобранные коды. Нужна сверка')
        scanned = d.get('mode') == 'scanned'
        if not scanned: require(p.get('confirm_zeroes') is True, 'Подтверди нули для несканированных позиций', 422)
        for item_id, row in d['items'].items():
            b = get_balance(s, item_id, d['location'])
            require(b.version == row['version'], 'После начала пересчёта были движения или резервы. Нужна сверка')
            restricted = b.reserved if scanned else b.reserved + b.frozen + b.damaged
            target = Decimal(row['count']) - restricted
            require(target >= 0, 'Посчитанное количество меньше резерва: ' + get_item(s, item_id).name)
            row['expected'] = str(b.free + (b.reserved if scanned else b.reserved + b.frozen + b.damaged)) if b.known else None
            row['difference'] = str(Decimal(row['count']) - Decimal(row['expected'])) if row['expected'] is not None else None
            b.known = True
            move(s, doc, item_id, d['location'], {'free': target - b.free})
        d.update(status='posted', posted_at=now().isoformat())
    save(e, d)
    return {'id': e.id, 'version': e.version}
