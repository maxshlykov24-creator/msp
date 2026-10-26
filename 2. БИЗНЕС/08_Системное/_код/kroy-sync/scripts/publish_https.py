"""Add the YUJI subpath to the existing TLS vhost, with validated rollback."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

config = Path('/etc/nginx/sites-available/ms-p.ru')
original = config.read_text()
marker = '# YUJI application subpath'
if marker not in original:
    assert 'location / {' in original and 'ssl_certificate ' in original
    backup = Path('/opt/kroy-app-stage/releases/nginx-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.conf')
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(config, backup)
    block = '''    # YUJI application subpath
    location = /yuji { return 301 /yuji/; }
    location ^~ /yuji/ {
        proxy_pass http://127.0.0.1:18764/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 2m;
        proxy_read_timeout 60s;
    }

'''
    config.write_text(original.replace('    location / {', block + '    location / {', 1))
    try:
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        config.write_text(original)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise
    print('HTTPS path enabled; prior vhost saved:', backup.name)
else:
    print('HTTPS path already configured')
