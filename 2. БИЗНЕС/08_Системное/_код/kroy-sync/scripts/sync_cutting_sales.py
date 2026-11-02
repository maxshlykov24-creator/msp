"""Root sales-reader process; API workers never receive marketplace credentials."""
import sys,os,json,time,fcntl
from pathlib import Path
sys.path.insert(0,'/opt/kroy-sync')
import config
sys.path.insert(0,'/opt/kroy-app-stage')
from app.db import engine_for
from app.sales import sync
from app.sales_jobs import claim,finish
for line in Path('/opt/kroy-app-stage/.env.app').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1);os.environ[k]=v
os.environ.setdefault('KROY_SALES_CACHE','/opt/kroy-app-stage/sales-cache')
engine=engine_for()
watch='--watch' in sys.argv
while True:
    with open('/opt/kroy-app-stage/sales-reader.lock','a') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if not watch:print('Sales reader already running');break
            time.sleep(2);continue
        if watch:
            job=claim(engine)
            if job:
                try:finish(engine,job['id'],sync(engine,os.environ,job['start'],job['end']))
                except Exception:finish(engine,job['id'],error='Сверка прервана. Повтори запрос')
        else:
            print(json.dumps(sync(engine,os.environ),ensure_ascii=False));break
    time.sleep(3)
