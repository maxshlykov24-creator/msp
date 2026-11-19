"""Monthly garment workshop worksheet backed by documented receipts and rates."""
import calendar
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Entity
from .service import require, get_item, create_entity, quantity

def month_bounds(month):
    require(isinstance(month,str) and len(month)==7,'Укажи месяц',422)
    try: start=date.fromisoformat(month+'-01')
    except ValueError:
        require(False,'Укажи месяц в формате ГГГГ-ММ',422)
    return str(start),str(start.replace(day=calendar.monthrange(start.year,start.month)[1]))

def record(s,doc,p):
    kind=p['kind']
    require(kind in ('receipt','rate','advance','payment','correction','opening'),'Неизвестный вид записи',422)
    effective=date.fromisoformat(p['effective_date'])
    month=p.get('month',str(effective)[:7])
    month_bounds(month)
    require(not s.get(Entity,'workshop:'+month),'Месяц уже закрыт. Внеси корректировку в открытый период')
    ref=str(p.get('reference','')).strip()
    require(ref and len(ref)<=200,'Укажи документ или основание',422)
    data=dict(kind=kind,effective_date=str(effective),month=month,reference=ref)
    rows=[e.data for e in s.scalars(select(Entity).where(Entity.kind=='finance'))]
    if kind in ('rate','receipt'):
        item=get_item(s,p.get('item_id'))
        require(item.kind=='garment','Выбери изделие',422)
        require(str(effective)[:7]==month,'Дата должна входить в месяц записи',422)
        data['item_id']=item.id
    if kind=='receipt':
        require(p.get('platform') in ('wb','ozon'),'Выбери площадку',422)
        data.update(platform=p['platform'],quantity=str(quantity(p['quantity'],item)))
        require(not any(r.get('kind')==kind and all(r.get(k)==data[k] for k in ('platform','reference','item_id')) for r in rows),'Такая строка акта уже внесена')
    else:
        amount=quantity(p.get('amount'),positive=False)
        require(amount==amount.quantize(Decimal('.01')) and (kind in ('opening','correction') or amount>0),'Проверь сумму в рублях и копейках',422)
        data['amount']=str(amount)
        if kind=='rate':
            require(not any(r.get('kind')=='rate' and r.get('item_id')==data['item_id'] and r.get('effective_date')==data['effective_date'] for r in rows),'Ставка на эту дату уже есть')
        elif kind=='opening':
            require(not any(r.get('kind')=='opening' and r.get('month')==month for r in rows),'Начальное сальдо уже внесено')
        else:
            require(not any(r.get('reference')==ref and r.get('kind') in ('advance','payment','correction') for r in rows),'Документ уже учтён')
    create_entity(s,doc,'finance',data)
    return {'id':doc}

def worksheet(s,month):
    start,end=month_bounds(month)
    closed=s.get(Entity,'workshop:'+month)
    if closed:return {**closed.data,'closed':True}
    records=[{**e.data,'id':e.id} for e in s.scalars(select(Entity).where(Entity.kind=='finance').order_by(Entity.id))]
    rates=[r for r in records if r['kind']=='rate' and r.get('item_id')]
    current=[r for r in records if r.get('month',r['effective_date'][:7])==month]
    receipts=[]
    problems=[]
    for r in current:
        if r['kind']!='receipt':continue
        rate=max((v for v in rates if v['item_id']==r['item_id'] and v['effective_date']<=r['effective_date']),key=lambda v:v['effective_date'],default=None)
        amount=Decimal(r['quantity'])*Decimal(rate['amount']) if rate else None
        receipts.append({**r,'rate':rate['amount'] if rate else None,'rate_id':rate['id'] if rate else None,'accrual':str(amount) if rate else None})
    if any(r['rate'] is None for r in receipts):problems.append('Есть приёмки без ставки на дату приёмки')
    if not receipts:problems.append('Нет документированных приёмок за месяц')
    if not any(r['kind']=='opening' for r in current):problems.append('Укажи начальное сальдо, включая нулевое')
    if any(not r.get('reference') for r in current if r['kind']!='rate'):problems.append('Проверь основания прежних записей')
    sums={k:sum((Decimal(r['amount']) for r in current if r['kind']==k),Decimal(0)) for k in ('advance','payment','correction','opening')}
    accrued=sum((Decimal(r['accrual']) for r in receipts if r['accrual'] is not None),Decimal(0))
    total=sums['opening']+accrued+sums['correction']-sums['advance']-sums['payment']
    report=dict(month=month,closed=False,receipts=receipts,entries=current,accrued=str(accrued),problems=problems,
                totals={k:str(v) for k,v in sums.items()},balance=None if problems else str(total))
    report['fingerprint']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    return report

def close(s,doc,p):
    report=worksheet(s,p['month'])
    require(not report['closed'] and not report['problems'] and p.get('confirmed') is True,'Сначала проверь полноту месяца и правила расчёта')
    require(month_bounds(p['month'])[1]<str(datetime.now(ZoneInfo('Europe/Moscow')).date()),'Месяц ещё не закончился')
    require(p.get('fingerprint')==report['fingerprint'],'Расчёт изменился. Проверь его ещё раз')
    create_entity(s,'workshop:'+p['month'],'workshop_close',{**report,'document_id':doc})
    return {'id':doc,'balance':report['balance']}
