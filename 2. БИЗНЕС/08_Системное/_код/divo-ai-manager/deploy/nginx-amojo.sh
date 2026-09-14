#!/usr/bin/env bash
# Врезает location /amojo/ в живой vhost divomotors-analytics.ru.
# Сертификат уже есть, DNS уже смотрит сюда, новый домен не нужен.
set -euo pipefail
CONF="${1:-/etc/nginx/sites-available/divo-analytics}"
BACKUP="${CONF}.bak-amojo-$(date +%Y%m%d%H%M)"

if grep -q 'location /amojo/' "$CONF"; then
  echo "location /amojo/ уже есть в $CONF"
else
  cp -a "$CONF" "$BACKUP"
  python3 - "$CONF" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
snippet = """
    location /amojo/ {
        proxy_pass http://127.0.0.1:19110;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 8s;
        proxy_connect_timeout 3s;
    }

"""
needle = "    location / {"
if needle not in text:
    raise SystemExit("не нашёл location / в %s" % path)
path.write_text(text.replace(needle, snippet + needle, 1))
print("врезал /amojo/ в", path)
PY
fi

nginx -t
systemctl reload nginx
echo "nginx reload ok"
