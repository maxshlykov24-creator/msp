#!/bin/sh
# Хост запускает этот файл в понедельник 07:00 Europe/Moscow.
# Неудачный запуск старый снимок не удаляет.
set -eu
cd /opt/mansband-kassa
mkdir -p /var/lock
exec 9>/var/lock/crm-analytics-refresh.lock
flock -n 9 || exit 0
docker compose exec -T api node scripts/reconcile-crm-analytics.mjs
