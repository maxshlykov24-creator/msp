#!/usr/bin/env bash
# Nginx + Let's Encrypt: поддомен → dkacademy-bot на 127.0.0.1:19082.
# На VPS (после A-записи поддомена на публичный IPv4 этой машины):
#   export DKACADEMY_FQDN=dkacademy-bot-xxxxx.twc1.net
#   bash deploy/timeweb-nginx-subdomain.sh
#
# Опционально: CERTBOT_EMAIL=you@example.com DKACADEMY_ROOT=/root/dkacademy-bot
set -euo pipefail

DKACADEMY_FQDN="${DKACADEMY_FQDN:?Укажите: export DKACADEMY_FQDN=....twc1.net}"
ROOT="${DKACADEMY_ROOT:-/root/dkacademy-bot}"

if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
  CERTBOT_EXTRA=(--email "$CERTBOT_EMAIL" --agree-tos --non-interactive)
else
  CERTBOT_EXTRA=(--register-unsafely-without-email --agree-tos --non-interactive)
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_SRC="${SCRIPT_DIR}/nginx/dkacademy-bot.subdomain.conf"
if [[ ! -f "$CFG_SRC" ]]; then
  echo "Не найден ${CFG_SRC}" >&2
  exit 1
fi
sed "s/__DKACADEMY_FQDN__/${DKACADEMY_FQDN}/g" "$CFG_SRC" > /etc/nginx/sites-available/dkacademy-bot-subdomain
ln -sf /etc/nginx/sites-available/dkacademy-bot-subdomain /etc/nginx/sites-enabled/dkacademy-bot-subdomain
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl reload nginx

if certbot certificates 2>/dev/null | grep -q "Certificate Name: ${DKACADEMY_FQDN}"; then
  echo "Сертификат для ${DKACADEMY_FQDN} уже есть, пропускаю certbot"
else
  certbot --nginx -d "$DKACADEMY_FQDN" "${CERTBOT_EXTRA[@]}"
fi

ENV_FILE="${ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
  REDIRECT="https://${DKACADEMY_FQDN}/oauth/callback"
  if grep -q '^REDIRECT_URI=' "$ENV_FILE"; then
    sed -i "s|^REDIRECT_URI=.*|REDIRECT_URI=${REDIRECT}|" "$ENV_FILE"
  else
    echo "REDIRECT_URI=${REDIRECT}" >> "$ENV_FILE"
  fi
  echo "В ${ENV_FILE} выставлен REDIRECT_URI=${REDIRECT}"
  echo "Убедитесь, что в amo интеграции тот же redirect_uri. Перезапустите контейнер: cd ${ROOT} && docker compose up -d"
else
  echo "Нет ${ENV_FILE} — пропишите REDIRECT_URI=https://${DKACADEMY_FQDN}/oauth/callback вручную." >&2
fi

echo "Проверка: curl -sS https://${DKACADEMY_FQDN}/health"
curl -sfS "https://${DKACADEMY_FQDN}/health" && echo || true
