"""Install localhost-only evaluation app without touching kroy-watch."""
import os
from pathlib import Path
import subprocess
root = Path('/opt/kroy-app-stage')
values = dict(line.split('=', 1) for line in (root / '.env.postgres').read_text().splitlines())
env = root / '.env.app'
fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write('KROY_DATABASE_URL=postgresql+psycopg://kroy:' + values['POSTGRES_PASSWORD'] + '@127.0.0.1:55436/kroy_stage\nKROY_ENV=stage\n')
settings = dict(line.split('=', 1) for line in env.read_text().splitlines())
runtime = {**os.environ, **settings}
subprocess.run(['python3', '-m', 'venv', str(root / '.venv')], check=True)
python = str(root / '.venv/bin/python')
subprocess.run([python, '-m', 'pip', 'install', '-q', '-r', str(root / 'requirements-app.txt')], check=True)
subprocess.run([python, '-m', 'app.manage', 'init'], cwd=root, env=runtime, check=True)
if not (root / 'owner.key').exists():
    subprocess.run([python, '-m', 'app.manage', 'user', '--id', 'maxim-stage', '--name', 'Максим', '--role', 'owner',
                    '--token-file', str(root / 'owner.key')], cwd=root, env=runtime, check=True)
subprocess.run([python, '-m', 'app.import_snapshot', str(root / 'snapshot.json')], cwd=root, env=runtime, check=True)
# Run as a dedicated OS account, not root.
if subprocess.run(['id', '-u', 'kroy-stage'], capture_output=True).returncode:
    subprocess.run(['useradd', '--system', '--home-dir', str(root), '--shell', '/usr/sbin/nologin', 'kroy-stage'], check=True)
subprocess.run(['chown', '-R', 'kroy-stage:kroy-stage', str(root)], check=True)
unit = '''[Unit]
Description=YUJI evaluation application (no external publication)
After=network.target docker.service
[Service]
User=kroy-stage
Group=kroy-stage
WorkingDirectory=/opt/kroy-app-stage
EnvironmentFile=/opt/kroy-app-stage/.env.app
ExecStart=/opt/kroy-app-stage/.venv/bin/python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 18764
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
MemoryMax=256M
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/kroy-app-stage.service').write_text(unit)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'kroy-app-stage'], check=True)
print('Localhost-only stage app installed; production watcher untouched')
