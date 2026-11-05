"""Orders worker: one process-wide/account-aware reader, short DB transactions."""
import os,sys,json,time,fcntl
from pathlib import Path
from datetime import datetime
ROOT=Path(os.environ.get('KROY_APP_ROOT','/opt/kroy-app-stage'))
sys.path.insert(0,'/opt/kroy-sync');import config
sys.path.insert(0,str(ROOT))
from app.db import engine_for,Entity,now,transaction
from app.order_sync import run
from app.order_feed import sync_state,set_sync
from app.order_accounting import reconcile
for line in (ROOT/'.env.app').read_text().splitlines():
 if '=' in line:k,v=line.split('=',1);os.environ[k]=v
engine=engine_for();watch='--watch' in sys.argv;last=0
with (ROOT/'orders-reader.lock').open('a') as lock:
 os.chmod(lock.name,0o600)
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Orders worker already running')
 while True:
  state=sync_state(engine);request=state.get('request',{});requested=request.get('requested_at','')>request.get('handled_at','')
  if not watch or time.time()-last>=300 or requested:
   set_sync(engine,'cycle',state='running',started_at=now().isoformat())
   try:
    with transaction(engine) as s:
     snaps=[e.data for e in s.query(Entity).filter(Entity.kind=='snapshot')];location=next((d.get('location') for d in snaps if d.get('location')),None)
    result=run(engine,os.environ,history_steps=12 if '--backfill' in sys.argv else 1,on_current=(lambda:reconcile(engine,location)) if location else None)
    if location:accounting=reconcile(engine,location)
    else:accounting={'error':'Не задан склад нашего учёта'}
    bad=any(v.get('state') in ('error','waiting') for k,v in result.items() if k not in ('cycle','request','wb-identity'))
    set_sync(engine,'cycle',state='partial' if bad else 'ready',finished_at=now().isoformat(),accounting=accounting)
    if not watch:print(json.dumps({'phases':{k:v.get('state') for k,v in result.items()},'accounting':accounting},ensure_ascii=False))
   except Exception:
    set_sync(engine,'cycle',state='error',error='Синхронизация прервана; данные сохранены',finished_at=now().isoformat())
    if not watch:raise
   finally:
    if requested:set_sync(engine,'request',handled_at=request['requested_at'])
    last=time.time()
  if not watch:break
  time.sleep(5)
