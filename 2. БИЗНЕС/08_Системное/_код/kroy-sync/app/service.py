"""All writers (UI, bot, imports) use these atomic, idempotent commands."""
import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from .db import Balance, Document, Entity, Item, Movement, Operation, Outbox, transaction

PERMISSIONS = {
    'owner': {'catalog', 'adjust', 'cut', 'freeze', 'count', 'orders', 'finance', 'settings', 'export'},
    'warehouse': {'adjust', 'freeze', 'count', 'orders'},
    'cutter': {'cut'}, 'viewer': set(),
}
COMMAND_RIGHT = {
    'item.create': 'catalog', 'stock.adjust': 'adjust', 'freeze.create': 'freeze',
    'freeze.release': 'freeze', 'freeze.extend': 'freeze', 'freeze.configure': 'settings', 'task.create': 'cut',
    'task.approve': 'cut', 'task.confirm': 'cut', 'task.cancel': 'cut',
    'count.create': 'count', 'count.scan': 'count', 'count.set': 'count', 'count.resolve': 'count', 'count.post': 'adjust',
    'order.create': 'orders', 'order.ship': 'orders', 'order.cancel': 'orders',
    'return.receive': 'orders', 'fbo.create': 'orders', 'fbo.dispatch': 'orders',
    'fbo.accept': 'orders', 'finance.record': 'finance',
}
BUCKETS = ('free', 'frozen', 'reserved', 'damaged')


class DomainError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status


def require(condition, message, status=409):
    if not condition:
        raise DomainError(message, status)


def quantity(raw, item=None, positive=True):
    require(not isinstance(raw, bool), 'Количество не может быть логическим значением', 422)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise DomainError('Некорректное количество', 422)
    require(value.is_finite(), 'Количество должно быть конечным', 422)
    require(abs(value) < Decimal('1000000000000'), 'Слишком большое количество', 422)
    require(value == value.quantize(Decimal('.001')), 'Не более трёх знаков после запятой', 422)
    if positive:
        require(value > 0, 'Количество должно быть больше нуля', 422)
    if item and item.unit in ('piece', 'roll'):
        require(value == value.to_integral_value(), 'Изделия и целые рулоны учитываются целыми числами', 422)
    return value


def get_item(s, item_id):
    item = s.get(Item, item_id)
    require(item is not None, 'Позиция не найдена', 404)
    return item


def get_balance(s, item_id, location):
    get_item(s, item_id)
    b = s.scalar(select(Balance).where(Balance.item_id == item_id, Balance.location == location))
    if b is None:
        b = Balance(item_id=item_id, location=location, known=False, free=0, frozen=0, reserved=0, damaged=0, version=0)
        s.add(b)
        s.flush()
    return b


def entity(s, entity_id, kind, version=None):
    e = s.get(Entity, entity_id)
    require(e is not None and e.kind == kind, 'Объект не найден', 404)
    if version is not None:
        require(type(version) is int and e.version == version, 'Объект изменился. Обнови экран')
    return e


def save(e, data):
    e.data = data
    e.version += 1


def move(s, doc_id, item_id, location, deltas):
    item = get_item(s, item_id)
    b = get_balance(s, item_id, location)
    require(b.known, 'Остаток неизвестен. Нужен подтверждённый начальный остаток')
    for bucket, raw in deltas.items():
        require(bucket in BUCKETS, 'Неизвестное состояние остатка', 422)
        q = quantity(raw, item, positive=False)
        require(getattr(b, bucket) + q >= 0, 'Недостаточно количества: ' + item.name + ' / ' + bucket)
        setattr(b, bucket, getattr(b, bucket) + q)
        if q:
            s.add(Movement(document_id=doc_id, item_id=item_id, location=location, bucket=bucket, quantity=q))
    b.version += 1
    s.add(Outbox(document_id=doc_id, topic='stock.changed',
                 payload={'item_id': item_id, 'location': location, 'version': b.version}, status='held'))
    return b


def lines(s, raw, kind=None):
    require(isinstance(raw, list) and 0 < len(raw) <= 1000, 'Нужны строки документа', 422)
    seen, out = set(), []
    for line in raw:
        item = get_item(s, line['item_id'])
        require(item.id not in seen, 'Объедини повторные строки одной позиции', 422)
        require(kind is None or item.kind == kind, 'Неверный вид позиции', 422)
        seen.add(item.id)
        out.append({'item_id': item.id, 'quantity': str(quantity(line['quantity'], item))})
    return out


def create_entity(s, entity_id, kind, data):
    require(s.get(Entity, entity_id) is None, 'Такой объект уже существует')
    e = Entity(id=entity_id, kind=kind, data=data, version=1)
    s.add(e)
    return e


def execute(engine, user, op_id, command, payload, reason):
    try:
        UUID(op_id)
    except (ValueError, TypeError):
        raise DomainError('Нужен UUID операции', 422)
    require(command in COMMAND_RIGHT, 'Неизвестная команда', 422)
    require(COMMAND_RIGHT[command] in PERMISSIONS.get(user.role, set()), 'Нет права на эту операцию', 403)
    require(isinstance(reason, str) and reason.strip(), 'Укажи основание операции', 422)
    body = {'command': command, 'payload': payload, 'reason': reason, 'actor': user.id}
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with transaction(engine) as s:
        # Read active state again inside the write transaction: revocation is immediate.
        from .db import User
        actor = s.get(User, user.id)
        require(actor is not None and actor.active and COMMAND_RIGHT[command] in PERMISSIONS.get(actor.role, set()), 'Доступ отозван', 403)
        old = s.get(Operation, op_id)
        if old:
            require(old.fingerprint == fingerprint, 'ID операции уже использован с другим содержанием')
            return old.result
        s.add(Document(id=op_id, kind=command, actor=user.id, reason=reason, payload=payload))
        result = dispatch(s, op_id, command, copy.deepcopy(payload))
        result = {'document_id': op_id, **result}
        s.add(Operation(id=op_id, fingerprint=fingerprint, result=result))
        s.flush()
        return result


def dispatch(s, doc, command, p):
    if command == 'item.create':
        require(p.get('kind') in ('garment', 'material'), 'Вид: изделие или материал', 422)
        require(p.get('unit') in ('piece', 'roll', 'meter'), 'Неизвестная единица', 422)
        require(p['kind'] != 'garment' or p['unit'] == 'piece', 'Изделие учитывается в штуках', 422)
        require(s.get(Item, p['id']) is None, 'ID позиции уже существует')
        require(p.get('name') and p.get('model'), 'Укажи название и модель', 422)
        codes = p.get('barcodes', [])
        require(isinstance(codes, list) and all(isinstance(c, str) and c.strip() for c in codes), 'Штрихкоды должны быть строками', 422)
        require(len(codes) == len(set(codes)), 'Повтор штрихкода', 422)
        for other in s.scalars(select(Item)):
            require(not set(codes).intersection(other.barcodes), 'Штрихкод уже привязан к другой позиции')
        photo = p.get('photo', '')
        require(not photo or photo.startswith('https://'), 'Фото: HTTPS-ссылка', 422)
        s.add(Item(id=p['id'], name=p['name'], model=p['model'], kind=p['kind'], unit=p['unit'],
                   color=p.get('color', ''), size=p.get('size', ''), category=p.get('category', ''),
                   barcodes=codes, photo=photo, source={'document': doc}))
        return {'item_id': p['id']}
    if command == 'stock.adjust':
        item = get_item(s, p['item_id'])
        b = get_balance(s, item.id, p['location'])
        require(b.version == p['version'], 'Остаток изменился. Повтори сверку')
        target = quantity(p['physical'], item, positive=False)
        require(target >= 0, 'Физический остаток не может быть отрицательным', 422)
        new_free = target - b.frozen - b.reserved - b.damaged
        require(new_free >= 0, 'Пересчёт ниже заморозки, резерва и брака. Нужна сверка')
        b.known = True
        move(s, doc, item.id, p['location'], {'free': new_free - b.free})
        return {'version': b.version}
    if command == 'freeze.create':
        item = get_item(s, p['item_id'])
        require(item.kind == 'garment', 'Заморозка доступна для изделий')
        q = quantity(p['quantity'], item)
        b = get_balance(s, item.id, p['location'])
        require(b.version == p['version'], 'Остаток изменился')
        until = deadline(p.get('until'))
        move(s, doc, item.id, p['location'], {'free': -q, 'frozen': q})
        e = create_entity(s, doc, 'freeze', {'item_id': item.id, 'location': p['location'], 'remaining': str(q),
                          'until': until, 'mode': 'manual', 'reminded': False})
        return {'id': e.id, 'version': e.version}
    if command.startswith('freeze.'):
        e = entity(s, p['id'], 'freeze', p['version'])
        d = copy.deepcopy(e.data)
        if command == 'freeze.release':
            q = quantity(p['quantity'], get_item(s, d['item_id']))
            require(q <= Decimal(d['remaining']), 'Количество больше остатка заморозки')
            move(s, doc, d['item_id'], d['location'], {'frozen': -q, 'free': q})
            d['remaining'] = str(Decimal(d['remaining']) - q)
        elif command == 'freeze.configure':
            require(p['mode'] in ('manual', 'auto'), 'Режим: manual или auto', 422)
            require(p['mode'] != 'auto' or d.get('until'), 'Для автоматического снятия нужен срок')
            d['mode'] = p['mode']
        else:
            d['until'], d['reminded'] = deadline(p['until']), False
        save(e, d)
        return {'id': e.id, 'version': e.version}
    if command == 'task.create':
        products = lines(s, p['products'], 'garment')
        materials = lines(s, p['materials'], 'material')
        e = create_entity(s, doc, 'task', {'title': p['title'], 'location': p['location'],
                'products': products, 'materials': materials, 'status': 'draft',
                'remaining_products': products, 'remaining_materials': materials, 'facts': []})
        return {'id': e.id, 'version': e.version}
    if command.startswith('task.'):
        e = entity(s, p['id'], 'task', p['version'])
        d = copy.deepcopy(e.data)
        if command == 'task.approve':
            require(d['status'] == 'draft', 'Утверждать можно только черновик')
            for line in d['materials']:
                q = Decimal(line['quantity'])
                move(s, doc, line['item_id'], d['location'], {'free': -q, 'reserved': q})
            d['status'] = 'approved'
        elif command == 'task.cancel':
            require(d['status'] in ('draft', 'approved', 'partial'), 'Задание уже закрыто')
            if d['status'] != 'draft':
                for line in d['remaining_materials']:
                    q = Decimal(line['quantity'])
                    move(s, doc, line['item_id'], d['location'], {'reserved': -q, 'free': q})
            d['status'] = 'cancelled'
        else:
            require(d['status'] in ('approved', 'partial'), 'Задание не утверждено или уже закрыто')
            products = lines(s, p.get('products', d['remaining_products']), 'garment')
            materials = lines(s, p.get('materials', d['remaining_materials']), 'material')
            require(p.get('confirm_actual') is True, 'Подтверди, что количества являются фактическими', 422)
            until = deadline(p.get('until'))
            for actual, key, bucket in ((materials, 'remaining_materials', 'reserved'), (products, 'remaining_products', 'frozen')):
                rest = {l['item_id']: Decimal(l['quantity']) for l in d[key]}
                for line in actual:
                    q = Decimal(line['quantity'])
                    require(line['item_id'] in rest and q <= rest[line['item_id']], 'Факт превышает остаток задания. Нужна корректировка задания')
                    move(s, doc, line['item_id'], d['location'], {bucket: -q if bucket == 'reserved' else q})
                    rest[line['item_id']] -= q
                    if bucket == 'frozen':
                        create_entity(s, doc + ':' + line['item_id'], 'freeze', {
                            'item_id': line['item_id'], 'location': d['location'], 'remaining': str(q),
                            'until': until, 'mode': 'manual', 'task_id': e.id, 'reminded': False})
                d[key] = [{'item_id': k, 'quantity': str(v)} for k, v in rest.items() if v]
            d['facts'].append(doc)
            if not d['remaining_products']:
                # Unused allocated cloth is released, never silently consumed.
                for line in d['remaining_materials']:
                    q = Decimal(line['quantity'])
                    move(s, doc, line['item_id'], d['location'], {'reserved': -q, 'free': q})
                d['remaining_materials'] = []
                d['status'] = 'complete'
            else:
                d['status'] = 'partial'
        save(e, d)
        return {'id': e.id, 'version': e.version, 'status': d['status']}
    if command == 'count.create':
        require(isinstance(p['item_ids'], list) and p['item_ids'] and len(set(p['item_ids'])) == len(p['item_ids']), 'Укажи уникальные позиции пересчёта', 422)
        snapshots = {}
        for item_id in p['item_ids']:
            b = get_balance(s, item_id, p['location'])
            snapshots[item_id] = {'version': b.version, 'count': '0'}
        e = create_entity(s, doc, 'count', {'location': p['location'], 'items': snapshots, 'unknown': [], 'status': 'draft'})
        return {'id': e.id, 'version': 1}
    if command.startswith('count.'):
        e = entity(s, p['id'], 'count', p['version'])
        d = copy.deepcopy(e.data)
        require(d['status'] == 'draft', 'Пересчёт уже проведён')
        if command == 'count.scan':
            require(isinstance(p['barcode'], str), 'Штрихкод должен быть строкой', 422)
            candidates = [i for i in s.scalars(select(Item)) if p['barcode'] in i.barcodes]
            if len(candidates) != 1 or candidates[0].id not in d['items']:
                d['unknown'].append({'barcode': p['barcode'], 'reason': 'Неизвестный, неоднозначный или вне области пересчёта'})
            else:
                i = candidates[0]
                require(i.unit in ('piece', 'roll'), 'Метры требуют ручного измерения', 422)
                d['items'][i.id]['count'] = str(Decimal(d['items'][i.id]['count']) + 1)
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
                require(p['item_id'] in d['items'], 'Позиция вне области пересчёта')
                i = get_item(s, p['item_id'])
                require(i.unit in ('piece', 'roll'), 'Метры требуют измерения')
                d['items'][i.id]['count'] = str(Decimal(d['items'][i.id]['count']) + 1)
            row['resolved'] = True
            row['resolution'] = p.get('item_id') or 'ignored_out_of_scope'
            row['document_id'] = doc
        else:
            require(not any(not row.get('resolved') for row in d['unknown']), 'Есть неразобранные коды. Нужна сверка')
            require(p.get('confirm_zeroes') is True, 'Подтверди нули для несканированных позиций', 422)
            for item_id, row in d['items'].items():
                b = get_balance(s, item_id, d['location'])
                require(b.version == row['version'], 'После начала пересчёта были движения или резервы. Нужна сверка')
                target = Decimal(row['count']) - b.frozen - b.reserved - b.damaged
                require(target >= 0, 'Пересчёт ниже ограниченного количества')
                b.known = True
                move(s, doc, item_id, d['location'], {'free': target - b.free})
            d['status'] = 'posted'
        save(e, d)
        return {'id': e.id, 'version': e.version}
    if command in ('order.create', 'fbo.create'):
        kind = 'order' if command == 'order.create' else 'fbo'
        require(p['platform'] in ('wb', 'ozon', 'manual'), 'Неизвестная площадка', 422)
        # Namespaced across platform, account, object kind. No date cutoff drops open orders.
        key = json.dumps([kind, p['platform'], p['account'], p['external_id']], ensure_ascii=False, separators=(',', ':'))
        entity_id = kind + ':' + hashlib.sha256(key.encode()).hexdigest()
        require(s.get(Entity, entity_id) is None, 'Заказ или поставка уже зарегистрированы')
        ll = lines(s, p['lines'], 'garment')
        reserved, shortages = [], []
        for line in ll:
            b = get_balance(s, line['item_id'], p['location'])
            q = Decimal(line['quantity'])
            take = min(b.free, q) if b.known else Decimal(0)
            if take:
                move(s, doc, line['item_id'], p['location'], {'free': -take, 'reserved': take})
            reserved.append({'item_id': line['item_id'], 'quantity': str(take)})
            if take < q:
                shortages.append({'item_id': line['item_id'], 'quantity': str(q - take)})
        e = create_entity(s, entity_id, kind, {**p, 'lines': ll, 'reserved': reserved,
                           'shortages': shortages, 'status': 'shortage' if shortages else 'reserved'})
        return {'id': e.id, 'version': 1, 'shortages': shortages}
    if command in ('order.ship', 'order.cancel', 'fbo.dispatch', 'fbo.accept'):
        kind = 'order' if command.startswith('order.') else 'fbo'
        e = entity(s, p['id'], kind, p['version'])
        d = copy.deepcopy(e.data)
        if command in ('order.ship', 'fbo.dispatch'):
            require(d['status'] == 'reserved' and not d['shortages'], 'Отправка требует полного резерва')
            for line in d['reserved']:
                q = Decimal(line['quantity'])
                move(s, doc, line['item_id'], d['location'], {'reserved': -q})
                if kind == 'fbo':
                    dest = 'transit:' + e.id
                    get_balance(s, line['item_id'], dest).known = True
                    move(s, doc, line['item_id'], dest, {'free': q})
            d['status'] = 'shipped' if kind == 'order' else 'transit'
            if kind == 'fbo':
                d['accepted'] = {l['item_id']: '0' for l in d['lines']}
        elif command == 'order.cancel':
            require(d['status'] in ('reserved', 'shortage', 'shipped'), 'Заказ уже отменён')
            if d['status'] != 'shipped':
                for line in d['reserved']:
                    q = Decimal(line['quantity'])
                    move(s, doc, line['item_id'], d['location'], {'reserved': -q, 'free': q})
                d['status'] = 'cancelled'
            else:
                d['status'] = 'return_pending'  # No fictitious receipt at seller's warehouse.
        else:
            require(d['status'] in ('transit', 'partial'), 'Поставка не в пути')
            actual = lines(s, p['lines'], 'garment')
            expected = {l['item_id']: Decimal(l['quantity']) for l in d['lines']}
            for line in actual:
                i, q = line['item_id'], Decimal(line['quantity'])
                require(i in expected and Decimal(d['accepted'][i]) + q <= expected[i], 'Пересорт или излишек: требуется отдельная сверка')
                move(s, doc, i, 'transit:' + e.id, {'free': -q})
                destination = 'marketplace:' + d['platform'] + ':' + d['account']
                get_balance(s, i, destination).known = True
                move(s, doc, i, destination, {'free': q})
                d['accepted'][i] = str(Decimal(d['accepted'][i]) + q)
            d['status'] = 'accepted' if all(Decimal(d['accepted'][i]) == q for i, q in expected.items()) else 'partial'
        save(e, d)
        return {'id': e.id, 'version': e.version, 'status': d['status']}
    if command == 'return.receive':
        e = entity(s, p['id'], 'order', p['version'])
        d = copy.deepcopy(e.data)
        require(d['status'] in ('shipped', 'return_pending', 'returned_partial'), 'Нет отгрузки для возврата')
        returned = d.get('returned', {})
        expected = {l['item_id']: Decimal(l['quantity']) for l in d['lines']}
        require(p['condition'] in ('good', 'damaged'), 'Укажи состояние возврата', 422)
        for line in lines(s, p['lines'], 'garment'):
            i, q = line['item_id'], Decimal(line['quantity'])
            require(i in expected and Decimal(returned.get(i, '0')) + q <= expected[i], 'Возврат превышает отгрузку')
            move(s, doc, i, d['location'], {'free' if p['condition'] == 'good' else 'damaged': q})
            returned[i] = str(Decimal(returned.get(i, '0')) + q)
        d['returned'] = returned
        d['status'] = 'returned' if all(Decimal(returned.get(i, '0')) == q for i, q in expected.items()) else 'returned_partial'
        save(e, d)
        return {'id': e.id, 'version': e.version}
    if command == 'finance.record':
        require(p['kind'] in ('advance', 'payment', 'rate', 'correction'), 'Неизвестная финансовая запись', 422)
        q = quantity(p['amount'], positive=False)
        require(q == q.quantize(Decimal('.01')), 'Деньги: не более двух знаков', 422)
        require(p['kind'] == 'correction' or q > 0, 'Нужна положительная сумма', 422)
        require(p.get('effective_date'), 'Укажи дату действия', 422)
        from datetime import date
        date.fromisoformat(p['effective_date'])
        e = create_entity(s, doc, 'finance', {**p, 'amount': str(q), 'calculation_status': 'rules_pending'})
        return {'id': e.id, 'final_payable': None, 'message': 'Правила расчёта цеха не подтверждены'}
    raise DomainError('Команда не реализована', 422)


def deadline(value):
    if not value:
        return None
    from datetime import datetime
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(d.tzinfo is not None, 'Укажи часовой пояс срока', 422)
    return d.isoformat()
