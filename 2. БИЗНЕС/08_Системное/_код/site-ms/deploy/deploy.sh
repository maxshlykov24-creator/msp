#!/usr/bin/env bash
# Изолированный деплой сайта ms-p.ru на уже работающий VPS (Nginx+Docker).
# Не останавливает другие compose-проекты и не правит чужие vhost.
#
# На VPS:
#   export MSP_SITE_ROOT=/opt/ms-p-site
#   export CERTBOT_EMAIL=info@ms-p.ru   # опционально
#   bash deploy/deploy.sh
#
# Требование: A-записи ms-p.ru и www.ms-p.ru → публичный IPv4 этой машины.
set -euo pipefail

DOMAIN_ROOT="${MSP_DOMAIN:-ms-p.ru}"
SITE_ROOT="${MSP_SITE_ROOT:-/opt/ms-p-site}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужен root" >&2
  exit 1
fi

command -v docker >/dev/null || { echo "Нужен docker" >&2; exit 1; }
command -v nginx >/dev/null || { echo "Нужен nginx" >&2; exit 1; }

echo "==> Каталог ${SITE_ROOT}"
mkdir -p "$SITE_ROOT"
# копируем только сайт + compose + deploy (без мусора)
rsync -a --delete \
  --exclude '.git' \
  --exclude '.DS_Store' \
  "${SRC_ROOT}/" "${SITE_ROOT}/"

echo "==> Docker compose (проект ms-p-site, порт 127.0.0.1:19091)"
cd "$SITE_ROOT"
# --force-recreate: гарантирует свежие bind-mount после rsync
docker compose up -d --pull missing --force-recreate
sleep 2
curl -sfS "http://127.0.0.1:19091/" >/dev/null
echo "    container OK"

echo "==> Nginx vhost ${DOMAIN_ROOT} (не трогаем другие сайты)"
install -m 644 "${SITE_ROOT}/deploy/nginx/ms-p.ru.conf" \
  "/etc/nginx/sites-available/${DOMAIN_ROOT}"
ln -sfn "/etc/nginx/sites-available/${DOMAIN_ROOT}" \
  "/etc/nginx/sites-enabled/${DOMAIN_ROOT}"
nginx -t
systemctl reload nginx

# DNS check (мягкий)
IP_A="$(dig +short "${DOMAIN_ROOT}" A | head -1 || true)"
IP_PUB="$(curl -4 -sfS --max-time 5 ifconfig.me || true)"
echo "    DNS ${DOMAIN_ROOT}=${IP_A:-?}  public=${IP_PUB:-?}"

if [[ -n "${IP_A}" && -n "${IP_PUB}" && "${IP_A}" == "${IP_PUB}" ]]; then
  if command -v certbot >/dev/null; then
    if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
      CERTBOT_EXTRA=(--email "$CERTBOT_EMAIL" --agree-tos --non-interactive)
    else
      CERTBOT_EXTRA=(--register-unsafely-without-email --agree-tos --non-interactive)
    fi
    echo "==> Certbot HTTPS"
    certbot --nginx -d "${DOMAIN_ROOT}" -d "www.${DOMAIN_ROOT}" \
      --redirect "${CERTBOT_EXTRA[@]}" || {
        echo "WARN: certbot не выпустил сертификат — проверь DNS и повтори:" >&2
        echo "  certbot --nginx -d ${DOMAIN_ROOT} -d www.${DOMAIN_ROOT}" >&2
      }
  else
    echo "WARN: certbot не установлен — HTTPS позже" >&2
  fi
else
  echo "WARN: DNS ещё не указывает на этот сервер — HTTPS пропущен."
  echo "      После A-записи: certbot --nginx -d ${DOMAIN_ROOT} -d www.${DOMAIN_ROOT}"
fi

echo "==> Проверка"
curl -sS -o /dev/null -w "localhost:19091 → %{http_code}\n" "http://127.0.0.1:19091/" || true
curl -sS -o /dev/null -w "Host ${DOMAIN_ROOT} → %{http_code}\n" \
  -H "Host: ${DOMAIN_ROOT}" "http://127.0.0.1/" || true

# не ломаем чужие сервисы — короткий smoke
if curl -sfS --max-time 3 "http://127.0.0.1:19082/health" >/dev/null 2>&1; then
  echo "    dkacademy-bot /health OK (не затронут)"
fi

echo "Готово. Перенос: rsync ${SITE_ROOT}/ → новый VPS + bash deploy/deploy.sh"
