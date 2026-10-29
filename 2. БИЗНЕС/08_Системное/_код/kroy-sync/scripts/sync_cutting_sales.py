"""Host-only read-only sales refresh. Never reads marketplace stock."""
import sys,os,json
from pathlib import Path
sys.path.insert(0,'/opt/kroy-sync')
import config
sys.path.insert(0,'/opt/kroy-app-stage')
from app.db import engine_for
from app.sales import sync
for line in Path('/opt/kroy-app-stage/.env.app').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1);os.environ[k]=v
print(json.dumps(sync(engine_for(),os.environ),ensure_ascii=False))
