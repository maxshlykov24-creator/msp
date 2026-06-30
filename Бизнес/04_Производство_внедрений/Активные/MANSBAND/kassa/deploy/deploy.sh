#!/usr/bin/env bash
# Деплой кассы MANSBAND по rsync (без GitHub) + docker compose up на VPS.
# Запускать с локальной машины из корня kassa/:  bash deploy/deploy.sh
#
# Переменные (можно через окружение или .env.deploy рядом):
#   VPS_HOST=72.56.240.103
#   VPS_USER=root
#   VPS_PORT=22
#   REMOTE_DIR=/opt/mansband-kassa
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Подхватить локальные настройки деплоя, если есть
[ -f deploy/.env.deploy ] && set -a && . deploy/.env.deploy && set +a

VPS_HOST="${VPS_HOST:-72.56.240.103}"
VPS_USER="${VPS_USER:-root}"
VPS_PORT="${VPS_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/opt/mansband-kassa}"

echo "== rsync кода на ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR} =="
rsync -az --delete \
  -e "ssh -p ${VPS_PORT}" \
  --exclude node_modules \
  --exclude dist \
  --exclude .git \
  --exclude '*.log' \
  --exclude '.env' \
  --exclude 'deploy/.env.deploy' \
  ./ "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"

echo "== Сборка и запуск на сервере =="
ssh -p "${VPS_PORT}" "${VPS_USER}@${VPS_HOST}" bash -lc "'
  set -euo pipefail
  cd ${REMOTE_DIR}
  if [ ! -f .env ]; then echo \"ОШИБКА: на сервере нет ${REMOTE_DIR}/.env (создай из .env.example)\"; exit 1; fi
  docker compose up -d --build
  echo \"== Миграции БД ==\"
  docker compose run --rm api node dist/db/migrate.js
  echo \"== Готово. Проверь: https://mansband-kassa.ru ==\"
'"

echo "Деплой завершён."
echo "Если пользователи ещё не созданы, выполни на сервере:"
echo "  cd ${REMOTE_DIR} && docker compose run --rm api node dist/seed.js"
