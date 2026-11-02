"""Install the dedicated read-only sales reader and its refresh queue consumer."""
from pathlib import Path
import subprocess
root=Path('/opt/kroy-app-stage')
(root/'sales-cache').mkdir(exist_ok=True,mode=0o700)
(root/'sales-reader.lock').touch(mode=0o600,exist_ok=True)
common='''After=network-online.target kroy-app-stage.service
[Service]
WorkingDirectory=/opt/kroy-app-stage
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/kroy-app-stage/sales-cache /opt/kroy-app-stage/sales-reader.lock
MemoryMax=256M
'''
Path('/etc/systemd/system/kroy-app-stage-sales-worker.service').write_text('''[Unit]
Description=YUJI.CLO sales refresh queue reader
'''+common+'''Type=simple
ExecStart=/opt/kroy-app-stage/.venv/bin/python scripts/sync_cutting_sales.py --watch
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
''')
Path('/etc/systemd/system/kroy-app-stage-sales.service').write_text('''[Unit]
Description=YUJI.CLO read-only periodic sales refresh
'''+common+'''Type=oneshot
ExecStart=/opt/kroy-app-stage/.venv/bin/python scripts/sync_cutting_sales.py
TimeoutStartSec=240
''')
Path('/etc/systemd/system/kroy-app-stage-sales.timer').write_text('''[Unit]
Description=YUJI.CLO periodic sales refresh
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','kroy-app-stage-sales-worker.service','kroy-app-stage-sales.timer'],check=True)
print('Read-only sales worker and timer installed')
