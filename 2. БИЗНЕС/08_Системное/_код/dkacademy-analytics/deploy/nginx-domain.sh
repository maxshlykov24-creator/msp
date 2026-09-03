#!/usr/bin/env bash
# Nginx vhost + Let's Encrypt для dkacademy-analytics.ru → 127.0.0.1:19090.
# Запускать на сервере ПОСЛЕ того, как A-запись домена указывает на этот IP:
#   export FQDN=dkacademy-analytics.ru
#   export CERTBOT_EMAIL=you@example.com   # опционально
#   bash deploy/nginx-domain.sh
#
# Скрипт НЕ трогает существующие конфиги — добавляет только свой vhost.
set -euo pipefail

FQDN="${FQDN:?Укажите: export FQDN=dkacademy-analytics.ru}"

if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
  CERTBOT_EXTRA=(--email "$CERTBOT_EMAIL" --agree-tos --non-interactive)
else
  CERTBOT_EXTRA=(--register-unsafely-without-email --agree-tos --non-interactive)
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_SRC="${SCRIPT_DIR}/nginx/dkacademy-analytics.conf"
[[ -f "$CFG_SRC" ]] || { echo "Не найден ${CFG_SRC}" >&2; exit 1; }

sed "s/__FQDN__/${FQDN}/g" "$CFG_SRC" > /etc/nginx/sites-available/dkacademy-analytics
ln -sf /etc/nginx/sites-available/dkacademy-analytics /etc/nginx/sites-enabled/dkacademy-analytics

nginx -t
systemctl reload nginx

if certbot certificates 2>/dev/null | grep -q "Certificate Name: ${FQDN}"; then
  echo "Сертификат для ${FQDN} уже есть, пропускаю certbot"
else
  certbot --nginx -d "$FQDN" "${CERTBOT_EXTRA[@]}"
fi

systemctl reload nginx
echo "Готово. Проверка: curl -sS https://${FQDN}/health"
curl -sfS "https://${FQDN}/health" && echo || true
