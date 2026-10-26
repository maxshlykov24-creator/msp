from pathlib import Path
import subprocess

for name, schedule in [('backup', 'OnCalendar=*-*-* 03:20:00 UTC\nPersistent=true'),
                       ('health', 'OnBootSec=2min\nOnUnitActiveSec=5min')]:
    unit = 'kroy-app-stage-' + name
    Path('/etc/systemd/system/' + unit + '.service').write_text('''[Unit]
Description=YUJI ''' + name + '''
After=docker.service kroy-app-stage.service
[Service]
Type=oneshot
WorkingDirectory=/opt/kroy-app-stage
ExecStart=/opt/kroy-app-stage/.venv/bin/python scripts/maintenance.py ''' + name + '''
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=128M
TimeoutStartSec=120
''')
    Path('/etc/systemd/system/' + unit + '.timer').write_text('''[Unit]
Description=YUJI scheduled ''' + name + '''
[Timer]
''' + schedule + '''
[Install]
WantedBy=timers.target
''')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
for name in ('backup', 'health'):
    subprocess.run(['systemctl', 'enable', '--now', 'kroy-app-stage-' + name + '.timer'], check=True)
    subprocess.run(['systemctl', 'start', 'kroy-app-stage-' + name + '.service'], check=True)
