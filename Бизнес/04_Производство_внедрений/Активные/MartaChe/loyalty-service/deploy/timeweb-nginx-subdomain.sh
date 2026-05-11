#!/usr/bin/env bash
# Nginx + Let's Encrypt на VPS: отдельный поддомен → loyalty (127.0.0.1:18080).
# Запуск на сервере: LOYALTY_FQDN=loyalty-xxx.twc1.net [CERTBOT_EMAIL=you@mail.tld] ./timeweb-nginx-subdomain.sh
#
# До запуска в панели Timeweb: поддомен, A-запись на публичный IPv4 этой VPS, порты 80/443.
set -euo pipefail

LOYALTY_FQDN="${LOYALTY_FQDN:?Укажите: export LOYALTY_FQDN=loyalty-....twc1.net}"
ENV_FILE="${ENV_FILE:-/root/loyalty-service/.env}"
ROOT="${ROOT:-/root/loyalty-service}"
WEBHOOK_URL="https://${LOYALTY_FQDN}/webhook/moysklad"

if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
  CERTBOT_EXTRA=(--email "$CERTBOT_EMAIL" --agree-tos --non-interactive)
else
  CERTBOT_EXTRA=(--register-unsafely-without-email --agree-tos --non-interactive)
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_SRC="${SCRIPT_DIR}/nginx/loyalty-subdomain.conf"
if [[ ! -f "$CFG_SRC" ]]; then
  echo "Не найден ${CFG_SRC}" >&2
  exit 1
fi
sed "s/__LOYALTY_FQDN__/${LOYALTY_FQDN}/g" "$CFG_SRC" > /etc/nginx/sites-available/loyalty-subdomain
ln -sf /etc/nginx/sites-available/loyalty-subdomain /etc/nginx/sites-enabled/loyalty-subdomain
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl reload nginx

if certbot certificates 2>/dev/null | grep -q "Certificate Name: ${LOYALTY_FQDN}"; then
  echo "Сертификат для ${LOYALTY_FQDN} уже есть, пропускаю certbot"
else
  certbot --nginx -d "$LOYALTY_FQDN" "${CERTBOT_EXTRA[@]}"
fi

if [[ -f "$ENV_FILE" ]]; then
  if grep -q '^WEBHOOK_PUBLIC_URL=' "$ENV_FILE"; then
    sed -i "s|^WEBHOOK_PUBLIC_URL=.*|WEBHOOK_PUBLIC_URL=${WEBHOOK_URL}|" "$ENV_FILE"
  else
    echo "WEBHOOK_PUBLIC_URL=${WEBHOOK_URL}" >> "$ENV_FILE"
  fi
  echo "Обновлён $ENV_FILE → WEBHOOK_PUBLIC_URL=$WEBHOOK_URL"
else
  echo "Нет $ENV_FILE — прописайте вручную: WEBHOOK_PUBLIC_URL=$WEBHOOK_URL" >&2
fi

if systemctl is-active --quiet loyalty.service 2>/dev/null; then
  systemctl restart loyalty.service
  echo "loyalty.service перезапущен"
fi

echo "Проверка: curl -sS https://${LOYALTY_FQDN}/health"
curl -sfS "https://${LOYALTY_FQDN}/health" && echo
echo "Далее: cd ${ROOT} && . .venv/bin/activate && python scripts/register_webhooks.py"
