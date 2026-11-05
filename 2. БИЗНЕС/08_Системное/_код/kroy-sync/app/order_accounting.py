"""Apply only observed, managed FBS transitions to our existing atomic ledger."""
import copy
import hashlib
import json
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select
from .db import Entity,User,Document,Balance,now,transaction
from .service import entity,require,save,dispatch,DomainError,execute
from .order_feed import PENDING
from .corrections import correct


def apply(s,doc,p):
    e=entity(s,p['id'],'market_order',p['version']);d=copy.deepcopy(e.data)
    existing=s.get(Entity,d.get('ledger_id',''))
    if existing and d['status']=='cancelled' and existing.data['status'] in ('reserved','shortage','shipped'):
        dispatch(s,doc,'order.cancel',dict(id=existing.id,version=existing.version))
        d['accounting_state']=existing.data['status'];d.pop('accounting_note',None);save(e,d)
        return {'id':e.id,'version':e.version,'accounting_state':d['accounting_state']}
    # FBO/FBW customer orders never write off our own warehouse a second time.
    if d['scheme']!='fbs':d['accounting_state']='marketplace'
    elif d['needs_mapping']:d['accounting_state']='needs_mapping'
    elif d['status'] in PENDING and not d.get('status_verified'):d['accounting_state']='needs_review';d['accounting_note']='Нужно подтвердить актуальный статус исторического заказа'
    elif not d.get('created_at'):d['accounting_state']='needs_review';d['accounting_note']='Нет даты заказа'
    else:
        ledger=s.get(Entity,d.get('ledger_id',''));status=d['status']
        if ledger is None and status not in PENDING and not capture_allowed(s,d,p['location']):
            d['accounting_state']='history'
            d['accounting_note']='Исторический заказ; повторное списание не выполняется'
        else:
            ll=[]
            for line in d['lines']:
                found=next((l for l in ll if l['item_id']==line['item_id']),None)
                if found:found['quantity']+=line['quantity']
                else:ll.append(dict(item_id=line['item_id'],quantity=line['quantity']))
            if ledger is None:
                # Link a previously entered exact external order rather than reserve twice.
                candidates=[]
                for external in {d['source_ref'],d['external_id']}:
                    key='order:'+hashlib.sha256(json.dumps(['order',d['platform'],d['account'],external],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                    existing=s.get(Entity,key)
                    if existing:candidates.append(existing)
                require(len(candidates)<=1,'Найдено несколько учётных документов одного заказа')
                if candidates:
                    ledger=candidates[0];require(ledger.data.get('managed_market_id') in (None,e.id),'Заказ уже связан с другой записью площадки');d['ledger_id']=ledger.id
                    save(ledger,{**ledger.data,'managed_market_id':e.id})
            if ledger is None:
                result=dispatch(s,doc,'order.create',dict(platform=d['platform'],account=d['account'],external_id=d['source_ref'],location=p['location'],lines=ll))
                ledger=s.get(Entity,result['id']);d['ledger_id']=ledger.id
                ledger.data={**ledger.data,'managed_market_id':e.id,'managed_since':now().isoformat()}
            if ledger.data.get('managed_market_id')!=e.id:save(ledger,{**ledger.data,'managed_market_id':e.id})
            ld=ledger.data
            if ld['status'] in ('reserved','shortage'):
                from decimal import Decimal
                expected={l['item_id']:Decimal(str(l['quantity'])) for l in ll};actual={l['item_id']:Decimal(l['quantity']) for l in ld['lines']}
                if expected!=actual and status in PENDING:
                    from .service import move,get_balance
                    for line in ld['reserved']:
                        q=Decimal(line['quantity'])
                        if q:move(s,doc,line['item_id'],ld['location'],{'reserved':-q,'free':q})
                    reserved=[];shortages=[]
                    for line in ll:
                        b=get_balance(s,line['item_id'],ld['location']);q=Decimal(str(line['quantity']));take=min(b.free,q) if b.known else Decimal(0)
                        if take:move(s,doc,line['item_id'],ld['location'],{'free':-take,'reserved':take})
                        reserved.append(dict(item_id=line['item_id'],quantity=str(take)))
                        if take<q:shortages.append(dict(item_id=line['item_id'],quantity=str(q-take)))
                    save(ledger,{**ld,'lines':[dict(item_id=l['item_id'],quantity=str(l['quantity'])) for l in ll],'reserved':reserved,'shortages':shortages,'status':'shortage' if shortages else 'reserved'})
                    ld=ledger.data
                elif expected!=actual:
                    d['accounting_state']='needs_review';d['accounting_note']='Состав отгруженного заказа отличается от зарезервированного';save(e,d);return {'id':e.id,'version':e.version}
            if status=='cancelled' and ld['status'] in ('reserved','shortage','shipped'):
                dispatch(s,doc,'order.cancel',dict(id=ledger.id,version=ledger.version))
            elif status in PENDING and ld['status']=='shortage':
                correct(s,doc,'order.reserve',dict(id=ledger.id,version=ledger.version))
            elif status in ('shipping','delivered','returned') and ld['status']=='reserved':
                dispatch(s,doc,'order.ship',dict(id=ledger.id,version=ledger.version))
            if status=='returned' and ledger.data['status']=='shipped':dispatch(s,doc,'order.cancel',dict(id=ledger.id,version=ledger.version))
            ld=ledger.data
            if status in ('shipping','delivered','returned') and ld['status']=='shortage' and covered_by_initial_count(s,d,ledger.data):
                save(ledger,{**ld,'status':'shipped','reserved':[],'initial_count_covered':True,'shortages':[]})
                ld=ledger.data
                if status=='returned':dispatch(s,doc,'order.cancel',dict(id=ledger.id,version=ledger.version));ld=ledger.data
            d['accounting_state']='covered_by_count' if ld['status']=='shipped' and ld.get('initial_count_covered') else ld['status'];d.pop('accounting_note',None)
            if ld['status']=='shortage':
                unknown=any(not (b:=s.scalar(select(Balance).where(Balance.item_id==line['item_id'],Balance.location==ld['location']))) or not b.known for line in ld['lines'])
                if unknown:d['accounting_state']='waiting_stock'
                if status in ('shipping','delivered','returned'):d['accounting_note']='Площадка подтвердила передачу. Списание ждёт сверки начального остатка'

    save(e,d)
    return {'id':e.id,'version':e.version,'accounting_state':d['accounting_state']}


def reconcile(engine,location):
    with transaction(engine) as s:
        user=s.get(User,'marketplace-orders-worker')
        if not user:
            # Service identity has no usable login token.
            user=User(id='marketplace-orders-worker',name='Синхронизация заказов',role='warehouse',active=True,token_hash=__import__('secrets').token_hex(32));s.add(user)
        jobs=[]
        own={e.id:e for e in s.scalars(select(Entity).where(Entity.kind=='order'))}
        balances={b.item_id:b for b in s.scalars(select(Balance).where(Balance.location==location))}
        initial=initial_times(s,location) if any(b.known for b in balances.values()) else {}
        gate=s.get(Entity,'order-sync:accounting-start')
        for e in s.scalars(select(Entity).where(Entity.kind=='market_order')):
            d=e.data;ledger=own.get(d.get('ledger_id',''))
            if ledger is None:
                candidates=[]
                for external in {d['source_ref'],d['external_id']}:
                    key='order:'+hashlib.sha256(json.dumps(['order',d['platform'],d['account'],external],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                    existing=own.get(key)
                    if existing:candidates.append(existing)
                if len(candidates)==1:
                    ledger=candidates[0];d={**d,'ledger_id':ledger.id};e.data=d
                elif len(candidates)>1:e.data={**d,'accounting_state':'needs_review','accounting_note':'Найдено несколько учётных документов'};continue
            if ledger is None and (d['scheme']!='fbs' or d['needs_mapping'] or d['status'] not in PENDING and not (gate and capture_allowed(s,d,location,initial,gate)) or d['status'] in PENDING and not d.get('status_verified')):
                label='needs_mapping' if d['needs_mapping'] else 'marketplace' if d['scheme']!='fbs' else 'needs_review' if d['status'] in PENDING and not d.get('status_verified') else 'history'
                if d.get('accounting_state')!=label:e.data={**d,'accounting_state':label}
                continue
            # A successful unchanged source state is not another warehouse operation.
            own_version=ledger.version if ledger else None
            balance_versions=[]
            if d.get('accounting_state') in ('shortage','waiting_stock'):
                for l in d['lines']:
                    if not l['item_id']:continue
                    b=balances.get(l['item_id']);balance_versions.append((l['item_id'],b.version if b else None))
            signature=hashlib.sha256(json.dumps([d['status'],d['lines'],d['scheme'],own_version,balance_versions],sort_keys=True).encode()).hexdigest()
            if d.get('accounting_signature')==signature:continue
            jobs.append((e.id,e.version,signature))
    result={'applied':0,'errors':0}
    for id,version,signature in jobs:
        try:
            execute(engine,user,str(uuid5(NAMESPACE_URL,'yuji-order:'+id+':'+signature)), 'market.order.apply',dict(id=id,version=version,location=location),'Сверка подтверждённого статуса заказа площадки')
            with transaction(engine) as s:
                e=s.get(Entity,id);d=dict(e.data)
                # Do not acknowledge an observation that arrived while accounting ran.
                if e.version==version+1:
                    ledger=s.get(Entity,d.get('ledger_id',''));own_version=ledger.version if ledger else None
                    balance_versions=[]
                    if d.get('accounting_state') in ('shortage','waiting_stock'):
                        for l in d['lines']:
                            b=s.scalar(select(Balance).where(Balance.item_id==l['item_id'],Balance.location==location));balance_versions.append((l['item_id'],b.version if b else None))
                    d['accounting_signature']=hashlib.sha256(json.dumps([d['status'],d['lines'],d['scheme'],own_version,balance_versions],sort_keys=True).encode()).hexdigest();e.data=d
            result['applied']+=1
        except DomainError as exc:
            with transaction(engine) as s:
                e=s.get(Entity,id)
                if e and e.version==version:e.data={**e.data,'accounting_state':'needs_review','accounting_note':exc.message}
            result['errors']+=1
    return result


def covered_by_initial_count(s,market,ledger):
    """A first physical count after observed handover already excludes those goods.

    Only explicit initialization documents qualify, and only if nothing was reserved.
    A later correction of an already known balance cannot erase a stock deduction.
    """
    from datetime import datetime
    from decimal import Decimal
    if any(Decimal(r['quantity']) for r in ledger['reserved']):return False
    observations=[h['at'] for h in market.get('history',[]) if h['status'] in ('shipping','delivered','returned')]
    if not observations:return False
    handed=datetime.fromisoformat(min(observations))
    for line in ledger['lines']:
        iid=line['item_id'];location=ledger['location'];dates=[]
        b=s.scalar(select(Balance).where(Balance.item_id==iid,Balance.location==location))
        if not b or not b.known:return False
        for doc in s.scalars(select(Document).where(Document.kind=='stock.adjust',Document.payload['item_id'].as_string()==iid,Document.payload['location'].as_string()==location)):
            if doc.payload.get('version')==0:dates.append(doc.created_at)
        for c in s.scalars(select(Entity).where(Entity.kind=='count',Entity.data['status'].as_string()=='posted',Entity.data['location'].as_string()==location)):
            row=c.data['items'].get(iid)
            if row is not None and 'expected' in row and row['expected'] is None and c.data.get('posted_at'):dates.append(datetime.fromisoformat(c.data['posted_at']))
        if not dates:return False
        from datetime import timezone
        if min(t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t for t in dates)<handed:return False
    return True


def initial_times(s,location):
    from datetime import datetime,timezone
    dates={}
    def add(iid,at):
        at=at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
        if iid not in dates or at<dates[iid]:dates[iid]=at
    for doc in s.scalars(select(Document).where(Document.kind=='stock.adjust',Document.payload['location'].as_string()==location)):
        if doc.payload.get('version')==0:add(doc.payload['item_id'],doc.created_at)
    for c in s.scalars(select(Entity).where(Entity.kind=='count',Entity.data['status'].as_string()=='posted',Entity.data['location'].as_string()==location)):
        if not c.data.get('posted_at'):continue
        for iid,row in c.data['items'].items():
            if 'expected' in row and row['expected'] is None:add(iid,datetime.fromisoformat(c.data['posted_at']))
    return dates


def capture_allowed(s,d,location,initial=None,gate=None):
    from datetime import datetime
    gate=gate or s.get(Entity,'order-sync:accounting-start')
    if not gate or d['scheme']!='fbs' or d['needs_mapping'] or d['status'] not in ('shipping','delivered','returned') or not d.get('status_verified') or not d.get('created_at'):return False
    if d['created_at']<gate.data['at']:return False
    reference=datetime.fromisoformat(d.get('handed_at') or d['created_at'])
    initial=initial if initial is not None else initial_times(s,location)
    return all(l['item_id'] in initial and initial[l['item_id']]<=reference for l in d['lines'])
