#!/usr/bin/env bash
# Деплой кассы MANSBAND по rsync (без GitHub) + docker compose up на VPS.
# Запускать с локальной машины из корня kassa/:  bash deploy/deploy.sh
#
# Переменные (можно через окружение или .env.deploy рядом):
#   VPS_HOST=72.56.240.103
#   VPS_USER=root
#   VPS_PORT=22
#   REMOTE_DIR=/opt/mansband-kassa
#   VPS_SSH_INTERFACE=en0  # необязательно: прямой интерфейс macOS при проблемах с VPN
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Подхватить локальные настройки деплоя, если есть
[ -f deploy/.env.deploy ] && set -a && . deploy/.env.deploy && set +a

VPS_HOST="${VPS_HOST:-72.56.240.103}"
VPS_USER="${VPS_USER:-root}"
VPS_PORT="${VPS_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/opt/mansband-kassa}"
VPS_SSH_INTERFACE="${VPS_SSH_INTERFACE:-}"

ssh_cmd=(ssh -p "${VPS_PORT}" -o ConnectTimeout=15)
rsync_ssh="ssh -p ${VPS_PORT} -o ConnectTimeout=15"
if [ -n "${VPS_SSH_INTERFACE}" ]; then
  if [[ ! "${VPS_SSH_INTERFACE}" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
    echo "Некорректный VPS_SSH_INTERFACE" >&2
    exit 1
  fi
  ssh_cmd+=(-o "ProxyCommand=nc -b ${VPS_SSH_INTERFACE} %h %p")
  rsync_ssh+=" -o 'ProxyCommand=nc -b ${VPS_SSH_INTERFACE} %h %p'"
fi

echo "== rsync кода на ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR} =="
rsync -az --delete \
  -e "${rsync_ssh}" \
  --exclude node_modules \
  --exclude dist \
  --exclude .git \
  --exclude '*.log' \
  --exclude '.env' \
  --exclude 'deploy/.env.deploy' \
  --exclude 'backups/' \
  ./ "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"

echo "== Сборка и запуск на сервере =="
"${ssh_cmd[@]}" "${VPS_USER}@${VPS_HOST}" \
  "chmod +x ${REMOTE_DIR}/deploy/remote-up.sh && bash ${REMOTE_DIR}/deploy/remote-up.sh"

echo "Деплой завершён."
echo "Если пользователи ещё не созданы, выполни на сервере:"
echo "  cd ${REMOTE_DIR} && docker compose run --rm api node dist/seed.js"
