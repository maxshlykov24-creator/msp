"""Host-only backup and monitoring; no marketplace or messaging writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path('/opt/kroy-app-stage')
for line in (ROOT / '.env.app').read_text().splitlines():
    k, v = line.split('=', 1)
    os.environ[k] = v
sys.path.insert(0, str(ROOT))
from app.db import Entity, engine_for, transaction


def status(name, data):
    with transaction(engine_for()) as s:
        e = s.get(Entity, 'maintenance:' + name)
        if e is None:
            s.add(Entity(id='maintenance:' + name, kind='maintenance', version=1, data=data))
        else:
            e.data = data
            e.version += 1


def backup():
    folder = ROOT / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = folder / (stamp + '.dump')
    if shutil.disk_usage(ROOT).free < 2 * 1024 ** 3:
        raise RuntimeError('Less than 2 GiB free; backup cannot start')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        subprocess.run(['docker', 'exec', 'kroy-app-stage-db', 'pg_dump', '-U', 'kroy', '-d', 'kroy_stage', '-n', 'public', '-Fc'], stdout=f, check=True)
    # Validate archive structure. Full recovery is a separate verification.
    with destination.open('rb') as f:
        subprocess.run(['docker', 'exec', '-i', 'kroy-app-stage-db', 'pg_restore', '--list'], stdin=f, stdout=subprocess.DEVNULL, check=True)
    status('backup', {'at': datetime.now(timezone.utc).isoformat(), 'ok': True, 'archive': destination.name,
                       'bytes': destination.stat().st_size, 'off_host': False})
    print('Database backup created and archive checked:', destination.name)


def monitor():
    checks = {}
    for unit in ('kroy-app-stage', 'kroy-app-stage-worker', 'kroy-watch'):
        checks[unit] = subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0
    free = shutil.disk_usage(ROOT).free
    status('health', {'at': datetime.now(timezone.utc).isoformat(), 'services': checks,
                       'free_bytes': free, 'ok': all(checks.values()) and free >= 2 * 1024 ** 3})
    print('Health observations updated')


if __name__ == '__main__':
    try:
        backup() if sys.argv[1] == 'backup' else monitor()
    except Exception as exc:
        status(sys.argv[1], {'at': datetime.now(timezone.utc).isoformat(), 'ok': False,
                             'error': type(exc).__name__})
        raise SystemExit('Maintenance failed: ' + type(exc).__name__)
