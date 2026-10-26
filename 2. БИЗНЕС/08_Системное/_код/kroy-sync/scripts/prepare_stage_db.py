"""Provision only the dedicated loopback-only staging PostgreSQL container."""
import os
from pathlib import Path
import secrets
import subprocess
root = Path('/opt/kroy-app-stage')
root.mkdir(mode=0o700, exist_ok=True)
env = root / '.env.postgres'
if not env.exists():
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('POSTGRES_USER=kroy\nPOSTGRES_DB=kroy_stage\nPOSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
result = subprocess.run(['docker', 'container', 'inspect', 'kroy-app-stage-db'], capture_output=True)
if result.returncode:
    subprocess.run(['docker', 'run', '-d', '--name', 'kroy-app-stage-db', '--restart', 'unless-stopped',
                    '--memory', '192m', '--cpus', '0.5', '--env-file', str(env),
                    '-p', '127.0.0.1:55436:5432', '-v', 'kroy-app-stage-pgdata:/var/lib/postgresql/data',
                    'postgres:16-alpine', '-c', 'shared_buffers=32MB', '-c', 'max_connections=25'], check=True)
print('Dedicated stage PostgreSQL provisioned; existing services untouched')
