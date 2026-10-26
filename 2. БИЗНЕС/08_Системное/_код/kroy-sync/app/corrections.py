"""Explicit linked corrections. Originals remain immutable."""
import copy
from decimal import Decimal
from sqlalchemy import select
from .db import Document, Movement
from .service import entity, save, require, move, get_balance, lines


def correct(s, doc, command, p):
    if command == 'task.reverse_fact':
        e = entity(s, p['id'], 'task', p['version'])
        d = copy.deepcopy(e.data)
        fact = p['fact_id']
        require(fact in d['facts'], 'Факт не относится к заданию')
        require(fact not in d.get('reversed_facts', []), 'Факт уже сторнирован')
        require(fact == [f for f in d['facts'] if f not in d.get('reversed_facts', [])][-1], 'Сначала сторнируй более поздние факты задания')
        require(d['status'] in ('complete', 'partial', 'approved'), 'Отменённое задание требует отдельной сверки')
        original = s.get(Document, fact)
        require(original is not None and original.kind == 'task.confirm', 'Не найден документ факта')
        movements = list(s.scalars(select(Movement).where(Movement.document_id == fact)))
        # Once the output leaves its original freeze, no automatic reversal is safe.
        outputs = [m for m in movements if m.bucket == 'frozen' and m.quantity > 0]
        for m in outputs:
            f = entity(s, fact + ':' + m.item_id, 'freeze')
            require(Decimal(f.data['remaining']) == m.quantity, 'Выпуск уже разморожен. Нужна сверка связанных движений')
        # Invert every original movement, including the final release of unused cloth.
        for m in reversed(movements):
            move(s, doc, m.item_id, m.location, {m.bucket: -m.quantity})
        for m in outputs:
            f = entity(s, fact + ':' + m.item_id, 'freeze')
            save(f, {**f.data, 'remaining': '0', 'reversed_by': doc})
        # Recompute remaining from immutable movement facts, ignoring reversals.
        reversed_facts = d.get('reversed_facts', []) + [fact]
        active = [f for f in d['facts'] if f not in reversed_facts]
        for key, bucket, sign in [('products', 'frozen', 1), ('materials', 'reserved', -1)]:
            done = {}
            for m in s.scalars(select(Movement).where(Movement.document_id.in_(active), Movement.bucket == bucket)):
                # Reserved releases are not actual consumption. Only count the
                # negative reserved rows without a matching free release.
                if key == 'materials':
                    continue
                done[m.item_id] = done.get(m.item_id, Decimal(0)) + sign * m.quantity
            if key == 'materials':
                for m in s.scalars(select(Movement).where(Movement.document_id.in_(active), Movement.bucket.in_(['reserved', 'free']))):
                    done[m.item_id] = done.get(m.item_id, Decimal(0)) - m.quantity
            remaining = []
            for line in d[key]:
                q = Decimal(line['quantity']) - done.get(line['item_id'], Decimal(0))
                require(q >= 0, 'История факта расходится с планом')
                if q:
                    remaining.append({'item_id': line['item_id'], 'quantity': str(q)})
            d['remaining_' + key] = remaining
        d['reversed_facts'] = reversed_facts
        d['status'] = 'partial' if active else 'approved'
        save(e, d)
        return {'id': e.id, 'version': e.version, 'reversed_fact': fact}
    if command == 'fbo.resolve':
        e = entity(s, p['id'], 'fbo', p['version'])
        d = copy.deepcopy(e.data)
        require(d['status'] in ('transit', 'partial'), 'Нет неразобранного товара в пути')
        require(p['resolution'] in ('returned', 'lost'), 'Укажи физический возврат или подтверждённую утрату')
        expected = {l['item_id']: Decimal(l['quantity']) for l in d['lines']}
        resolved = d.get('resolved', {})
        for line in lines(s, p['lines'], 'garment'):
            i, q = line['item_id'], Decimal(line['quantity'])
            require(i in expected and Decimal(d['accepted'].get(i, '0')) + Decimal(resolved.get(i, '0')) + q <= expected[i], 'Расхождение превышает неразобранный остаток')
            move(s, doc, i, 'transit:' + e.id, {'free': -q})
            if p['resolution'] == 'returned':
                require(p.get('condition') in ('good', 'damaged'), 'Укажи состояние возвращённого товара')
                move(s, doc, i, d['location'], {'free' if p['condition'] == 'good' else 'damaged': q})
            resolved[i] = str(Decimal(resolved.get(i, '0')) + q)
        d['resolved'] = resolved
        d['resolution_documents'] = d.get('resolution_documents', []) + [doc]
        if all(Decimal(d['accepted'].get(i, '0')) + Decimal(resolved.get(i, '0')) == q for i, q in expected.items()):
            d['status'] = 'settled'
        save(e, d)
        return {'id': e.id, 'version': e.version, 'status': d['status']}
    if command in ('order.reserve', 'fbo.reserve'):
        e = entity(s, p['id'], command.split('.')[0], p['version'])
        d = copy.deepcopy(e.data)
        require(d['status'] == 'shortage', 'Заказ не находится в дефиците')
        reserved = {l['item_id']: Decimal(l['quantity']) for l in d['reserved']}
        shortages = []
        for line in d['lines']:
            i = line['item_id']
            need = Decimal(line['quantity']) - reserved[i]
            b = get_balance(s, i, d['location'])
            take = min(b.free, need) if b.known else Decimal(0)
            if take:
                move(s, doc, i, d['location'], {'free': -take, 'reserved': take})
                reserved[i] += take
            if need > take:
                shortages.append({'item_id': i, 'quantity': str(need - take)})
        d['reserved'] = [{'item_id': i, 'quantity': str(q)} for i, q in reserved.items()]
        d['shortages'] = shortages
        d['status'] = 'shortage' if shortages else 'reserved'
        save(e, d)
        return {'id': e.id, 'version': e.version, 'status': d['status']}
