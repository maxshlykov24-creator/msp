"""Short DB-only refresh queue. Network reads run in the credentialed sales worker."""
import copy
from datetime import date, datetime, timedelta
from uuid import uuid4
from sqlalchemy import select
from .db import Entity, now, transaction
from .sales import MSK
from .service import require


def public_job(e):
    return {'id':e.id,**{k:v for k,v in e.data.items() if k!='actor'}}


def enqueue(engine, actor, raw):
    start=date.fromisoformat(raw['start']);end=date.fromisoformat(raw['end'])
    require(start<=end<datetime.now(MSK).date() and (end-start).days<90,'Выбери до 90 полных дней',422)
    with transaction(engine) as s:
        jobs=list(s.scalars(select(Entity).where(Entity.kind=='sales_refresh')))
        pending=0
        for e in jobs:
            d=e.data;age=(now()-datetime.fromisoformat(d['requested_at'])).total_seconds()
            if d['status'] in ('queued','running') and age>300:
                e.data={**d,'status':'failed','message':'Предыдущая сверка прервана. Повтори запрос'};e.version+=1;continue
            if d['status'] in ('queued','running'):pending+=1
            if d['start']==str(start) and d['end']==str(end) and (d['status'] in ('queued','running') or d['status']=='done' and age<30):return public_job(e)
        require(pending<5,'Уже выполняются сверки. Подожди завершения')
        e=Entity(id='sales-refresh:'+str(uuid4()),kind='sales_refresh',version=1,data=dict(start=str(start),end=str(end),status='queued',actor=actor,requested_at=now().isoformat()))
        s.add(e);s.flush();return public_job(e)


def claim(engine):
    with transaction(engine) as s:
        jobs=sorted((e for e in s.scalars(select(Entity).where(Entity.kind=='sales_refresh')) if e.data['status']=='queued'),key=lambda e:e.data['requested_at'])
        if not jobs:return None
        e=jobs[0];e.data={**e.data,'status':'running','started_at':now().isoformat()};e.version+=1
        return {'id':e.id,**copy.deepcopy(e.data)}


def finish(engine, job_id, result=None, error=None):
    with transaction(engine) as s:
        e=s.get(Entity,job_id);require(e is not None and e.kind=='sales_refresh','Сверка не найдена',404)
        e.data={**e.data,'status':'failed' if error else 'done','finished_at':now().isoformat(),**({'message':error} if error else {'results':result})};e.version+=1
