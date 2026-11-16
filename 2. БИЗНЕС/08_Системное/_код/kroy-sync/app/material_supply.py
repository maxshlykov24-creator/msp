"""Per-task cloth reservations. Purchase intentions never create stock."""
from decimal import Decimal
from .service import get_balance, move, require


def allocations(d):
    return {l['item_id']: Decimal(l['quantity']) for l in d.get('allocated_materials', d.get('remaining_materials', []))}


def reserve(s, doc, d, allow_purchase):
    allocated = allocations(d) if 'allocated_materials' in d else {}
    for line in d['remaining_materials']:
        iid, need = line['item_id'], Decimal(line['quantity'])
        current = allocated.get(iid, Decimal(0))
        b = get_balance(s, iid, d['location'])
        take = min(max(Decimal(0), need-current), max(Decimal(0), b.free)) if b.known else Decimal(0)
        require(allow_purchase or current+take == need, 'Недостаточно ткани. Разреши выдачу с докупкой')
        if take: move(s, doc, iid, d['location'], {'free': -take, 'reserved': take})
        allocated[iid] = current+take
    d['allocated_materials'] = [{'item_id':i,'quantity':str(q)} for i,q in allocated.items() if q]
    update_shortages(d)


def update_shortages(d):
    a = allocations(d)
    d['purchase_materials'] = [{'item_id':l['item_id'],'quantity':str(Decimal(l['quantity'])-a.get(l['item_id'],0))}
        for l in d['remaining_materials'] if Decimal(l['quantity']) > a.get(l['item_id'],0)]
    d['awaiting_materials'] = bool(d['purchase_materials'])


def release(s, doc, d):
    for iid,q in allocations(d).items():
        if q: move(s,doc,iid,d['location'],{'reserved':-q,'free':q})
    d['allocated_materials'] = []
    update_shortages(d)
