"""Apply only observed, managed FBS transitions to our existing atomic ledger."""
import copy
import hashlib
import json
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select
from .db import Entity,User,now,transaction
from .service import entity,require,save,dispatch,DomainError,execute
from .order_feed import PENDING


def apply(s,doc,p):
    e=entity(s,p['id'],'market_order',p['version']);d=copy.deepcopy(e.data)
    # FBO/FBW customer orders never write off our own warehouse a second time.
    if d['scheme']!='fbs':d['accounting_state']='marketplace'
    elif d['needs_mapping']:d['accounting_state']='needs_mapping'
    elif not d.get('created_at'):d['accounting_state']='needs_review';d['accounting_note']='Нет даты заказа'
    else:
        ledger=s.get(Entity,d.get('ledger_id',''));status=d['status']
        if ledger is None and status not in PENDING:
            d['accounting_state']='history'
            d['accounting_note']='Исторический заказ; повторное списание не выполняется'
        else:
            ll=[]
            for line in d['lines']:
                found=next((l for l in ll if l['item_id']==line['item_id']),None)
                if found:found['quantity']+=line['quantity']
                else:ll.append(dict(item_id=line['item_id'],quantity=line['quantity']))
            if ledger is None:
                result=dispatch(s,doc,'order.create',dict(platform=d['platform'],account=d['account'],external_id=d['source_ref'],location=p['location'],lines=ll))
                ledger=s.get(Entity,result['id']);d['ledger_id']=ledger.id
                ledger.data={**ledger.data,'managed_market_id':e.id,'managed_since':now().isoformat()}
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
                dispatch(s,doc,'order.reserve',dict(id=ledger.id,version=ledger.version))
            elif status in ('shipping','delivered') and ld['status']=='reserved':
                dispatch(s,doc,'order.ship',dict(id=ledger.id,version=ledger.version))
            ld=ledger.data;d['accounting_state']=ld['status'];d.pop('accounting_note',None)
    save(e,d)
    return {'id':e.id,'version':e.version,'accounting_state':d['accounting_state']}


def reconcile(engine,location):
    with transaction(engine) as s:
        user=s.get(User,'marketplace-orders-worker')
        if not user:
            # Service identity has no usable login token.
            user=User(id='marketplace-orders-worker',name='Синхронизация заказов',role='warehouse',active=True,token_hash=hashlib.sha256(('no-login:'+str(uuid5(NAMESPACE_URL,location))).encode()).hexdigest());s.add(user)
        jobs=[]
        for e in s.scalars(select(Entity).where(Entity.kind=='market_order')):
            d=e.data;ledger=s.get(Entity,d.get('ledger_id',''))
            # A successful unchanged source state is not another warehouse operation.
            own_version=ledger.version if ledger else None
            balance_versions=[]
            if d.get('accounting_state')=='shortage':
                from .db import Balance
                for l in d['lines']:
                    if not l['item_id']:continue
                    b=s.scalar(select(Balance).where(Balance.item_id==l['item_id'],Balance.location==location));balance_versions.append((l['item_id'],b.version if b else None))
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
                    if d.get('accounting_state')=='shortage':
                        from .db import Balance
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
