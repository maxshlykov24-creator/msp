#!/usr/bin/env bash
set -euo pipefail
SITE=/etc/nginx/sites-available/loyalty-subdomain
sed -i 's/server_name martache-pl.twc1.net martachepl.neurosell-tilda-ms-order-hook-5f6f.twc1.net;/server_name it-martache.ru martache-pl.twc1.net martachepl.neurosell-tilda-ms-order-hook-5f6f.twc1.net;/' "$SITE"
nginx -t
systemctl reload nginx
certbot --nginx -d it-martache.ru --register-unsafely-without-email --agree-tos --non-interactive --redirect
sed -i 's|^WEBHOOK_PUBLIC_URL=.*|WEBHOOK_PUBLIC_URL=https://it-martache.ru/webhook/moysklad|' /root/loyalty-service/.env
grep WEBHOOK_PUBLIC_URL /root/loyalty-service/.env
systemctl restart loyalty.service
cd /root/loyalty-service && . .venv/bin/activate && python scripts/register_webhooks.py
echo OK
