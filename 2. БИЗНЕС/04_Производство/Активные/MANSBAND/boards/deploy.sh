#!/usr/bin/env bash
# Выкладка досок воронок на mansband-kassa.ru/boards/
# Запуск из любой директории: bash …/MANSBAND/boards/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPS_HOST="${VPS_HOST:-72.56.240.103}"
VPS_USER="${VPS_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/var/www/mansband-boards}"

echo "== rsync ${ROOT} → ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR} =="
ssh -o ConnectTimeout=15 "${VPS_USER}@${VPS_HOST}" "mkdir -p ${REMOTE_DIR}"
rsync -az --delete --delete-excluded \
  -e "ssh -o ConnectTimeout=15" \
  --exclude 'deploy.sh' \
  --exclude '.DS_Store' \
  --exclude '*.csv' \
  "${ROOT}/" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"

echo "== проверка nginx location /boards/ =="
ssh -o ConnectTimeout=15 "${VPS_USER}@${VPS_HOST}" '
  if ! grep -q "location /boards/" /etc/nginx/sites-enabled/* /etc/nginx/sites-available/* 2>/dev/null; then
    echo "WARN: location /boards/ ещё не в nginx — добавь из kassa/deploy/nginx/mansband-kassa.conf и reload"
  fi
  curl -sf -o /dev/null -w "local_boards=%{http_code}\n" http://127.0.0.1/boards/ || \
  curl -sf -o /dev/null -w "local_boards_direct=%{http_code}\n" "file://${REMOTE_DIR}/index.html" || true
'

echo "Готово: https://mansband-kassa.ru/boards/"
