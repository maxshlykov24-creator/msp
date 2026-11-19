"""Apply one reviewed set of physical stock and frozen-quantity changes."""
from decimal import Decimal
from sqlalchemy import select
from .db import Document, Entity, uid
from .service import require, get_item, get_balance, move, quantity, create_entity, save


def apply_changes(s, doc, payload):
    location, rows = payload.get('location'), payload.get('rows')
    require(isinstance(location, str) and location.strip() and ':' not in location, 'Выбери склад учёта', 422)
    require(isinstance(rows, list) and 0 < len(rows) <= 500, 'Выбери позиции', 422)
    seen, changes = set(), []
    for row in rows:
        item = get_item(s, row['item_id'])
        require(item.kind == 'garment' and item.id not in seen, 'Выбери каждую позицию один раз', 422)
        seen.add(item.id)
        b = get_balance(s, item.id, location)
        require(type(row.get('version')) is int and b.version == row['version'], 'Остаток изменился. Проверь изменения ещё раз')
        old_total = b.free + b.reserved + b.frozen
        total = quantity(row['total'], item, positive=False)
        frozen = quantity(row['frozen'], item, positive=False)
        require(total >= 0 and frozen >= 0 and total >= frozen + b.reserved, 'Остаток меньше суммы резерва и заморозки: '+item.name, 422)
        require(b.known or row.get('initial') is True, 'Подтверди начальный остаток')
        if b.known and old_total == total and b.frozen == frozen:
            continue
        delta = frozen - b.frozen
        changes.append(dict(item_id=item.id,before_total=str(old_total) if b.known else None,after_total=str(total),
                            before_frozen=str(b.frozen) if b.known else None,after_frozen=str(frozen)))
        if delta > 0:
            create_entity(s, uid(), 'freeze', dict(item_id=item.id,location=location,remaining=str(delta),until=None,mode='manual',reminded=False,document_id=doc))
        elif delta < 0:
            releases=row.get('releases',{})
            require(isinstance(releases,dict) and sum((quantity(v,item,positive=False) for v in releases.values()),Decimal(0)) == -delta,
                    'Укажи, из каких замороженных партий снять количество',422)
            for batch_id, raw in releases.items():
                take=quantity(raw,item)
                batch=s.get(Entity,batch_id)
                require(batch and batch.kind=='freeze' and batch.data['item_id']==item.id and batch.data['location']==location,'Партия не соответствует позиции')
                remaining=Decimal(batch.data['remaining'])
                require(take<=remaining,'Остаток партии изменился')
                save(batch,{**batch.data,'remaining':str(remaining-take)})
        b.known = True
        move(s, doc, item.id, location, {'free':total-b.reserved-frozen-b.free, 'frozen':delta})
    require(changes,'Количество не изменилось',422)
    document=s.get(Document,doc)
    document.payload={**document.payload,'changes':changes}
    return {'changed':len(changes)}
