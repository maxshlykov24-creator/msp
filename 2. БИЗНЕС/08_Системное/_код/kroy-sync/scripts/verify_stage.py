"""Stage-only live readback + full regression in isolated PostgreSQL schemas."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request
root = Path('/opt/kroy-app-stage')
os.chdir(root)
import sys
sys.path.insert(0, str(root))
for line in (root / '.env.app').read_text().splitlines():
    k, v = line.split('=', 1)
    os.environ[k] = v
from app.db import engine_for
from app.import_marketplaces import import_observation
from app.import_photos import import_photos
from scripts.import_probe import import_probe
engine = engine_for()
print('marketplaces', json.dumps(import_observation(engine, json.loads(Path('marketplaces-snapshot.json').read_text())), ensure_ascii=False))
print('photos', import_photos(engine, json.loads(Path('wb-catalog.json').read_text())))
import_probe(engine, json.loads(Path('marketplace-probe-final.json').read_text()))
worker = '''[Unit]
Description=YUJI stage expiry reminders
After=kroy-app-stage.service
[Service]
User=kroy-stage
Group=kroy-stage
WorkingDirectory=/opt/kroy-app-stage
EnvironmentFile=/opt/kroy-app-stage/.env.app
ExecStart=/opt/kroy-app-stage/.venv/bin/python -m app.worker
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
MemoryMax=128M
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/kroy-app-stage-worker.service').write_text(worker)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'restart', 'kroy-app-stage'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'kroy-app-stage-worker'], check=True)
os.environ['KROY_TEST_DATABASE_URL'] = os.environ['KROY_DATABASE_URL']
subprocess.run([str(root/'.venv/bin/python'), '-m', 'pytest', 'tests/test_app.py', '-q'], check=True)
req = urllib.request.Request('http://127.0.0.1:18764/api/catalog', headers={'Authorization': 'Bearer ' + Path('owner.key').read_text().strip()})
with urllib.request.urlopen(req, timeout=10) as response:
    data = json.load(response)
print(json.dumps({'stage_catalog_rows': len(data['items']), 'with_photo': sum(bool(i['photo']) for i in data['items']),
                  'ledger_balances': sum(len(i['balances']) for i in data['items'])}))
subprocess.run(['systemctl', 'is-active', 'kroy-watch', 'kroy-app-stage', 'kroy-app-stage-worker'], check=True)
