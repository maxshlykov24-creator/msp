#!/usr/bin/env bash
# Перенос бота на новый сервер без потери данных.
# Запуск локально: OLD=213.165.44.164 NEW=<new_ip> ./deploy/migrate.sh
set -euo pipefail

OLD="${OLD:?укажите OLD=<ip старого>}"
NEW="${NEW:?укажите NEW=<ip нового>}"
PROJ_LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> 1. Остановка бота на старом сервере"
ssh -o StrictHostKeyChecking=accept-new root@"$OLD" 'systemctl stop kombikorm-bot || true'

echo ">>> 2. Забираем БД и бэкапы со старого"
TMP="$(mktemp -d)"
rsync -az "root@${OLD}:/var/lib/kombikorm-bot/" "${TMP}/data/"
scp "root@${OLD}:/opt/kombikorm-bot/.env" "${TMP}/.env"

echo ">>> 3. Разворачиваем код на новом (deploy.sh)"
HOST="$NEW" "${PROJ_LOCAL}/deploy/deploy.sh"

echo ">>> 4. Переносим данные и .env на новый"
ssh root@"$NEW" 'mkdir -p /var/lib/kombikorm-bot'
rsync -az "${TMP}/data/" "root@${NEW}:/var/lib/kombikorm-bot/"
scp "${TMP}/.env" "root@${NEW}:/opt/kombikorm-bot/.env"
ssh root@"$NEW" 'chown -R kombikorm:kombikorm /var/lib/kombikorm-bot /opt/kombikorm-bot/.env && chmod 600 /opt/kombikorm-bot/.env && systemctl restart kombikorm-bot'

echo ">>> Готово. Проверьте: ssh root@${NEW} 'journalctl -u kombikorm-bot -f'"
echo ">>> Старый сервер: kombikorm-bot остановлен (можно удалить позже)."
rm -rf "$TMP"
