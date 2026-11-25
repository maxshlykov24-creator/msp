"""Root sales-reader process; API workers never receive marketplace credentials."""
import sys,os,json,time,fcntl
from pathlib import Path
sys.path.insert(0,'/opt/kroy-sync')
import config
sys.path.insert(0,'/opt/kroy-app-stage')
from app.db import engine_for
from app.sales import sync,begin_sales_run,finish_sales_run,annotate_saved_gaps
from app.fbo_stock import sync_ozon
from app.sales_jobs import claim,finish
from app.service import DomainError
for line in Path('/opt/kroy-app-stage/.env.app').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1);os.environ[k]=v
os.environ.setdefault('KROY_SALES_CACHE','/opt/kroy-app-stage/sales-cache')
engine=engine_for()
watch='--watch' in sys.argv
annotated=False

def annotate():
    return annotate_saved_gaps(engine,os.environ.get('KROY_SALES_CACHE','/opt/kroy-app-stage/sales-cache'))

def guarded(source,start=None,end=None):
    begin_sales_run(engine,source)
    try:
        annotate()
        sync_ozon(engine,os.environ)
        result=sync(engine,os.environ,start,end)
    except Exception as exc:
        message=exc.message if isinstance(exc,DomainError) else str(exc)
        finish_sales_run(engine,(message or type(exc).__name__)[:300])
        raise
    finish_sales_run(engine)
    return result

while True:
    with open('/opt/kroy-app-stage/sales-reader.lock','a') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if not watch:print('Sales reader already running');break
            time.sleep(2);continue
        if '--stocks-only' in sys.argv:
            print(json.dumps(sync_ozon(engine,os.environ),ensure_ascii=False));break
        if watch and not annotated:
            try:annotate()
            except Exception:pass
            annotated=True
        if watch:
            job=claim(engine)
            if job:
                try:finish(engine,job['id'],guarded('manual',job['start'],job['end']))
                except Exception:finish(engine,job['id'],error='Сверка прервана. Повтори запрос')
        else:
            print(json.dumps(guarded('timer'),ensure_ascii=False));break
    time.sleep(3)
