#!/usr/bin/env bash
# Запускается на VPS из /opt/mansband-kassa после rsync.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "ОШИБКА: нет ${ROOT}/.env (создай из .env.example)"
  exit 1
fi

echo "== Резервная копия БД перед деплоем =="
mkdir -p backups
if docker compose ps -q db | grep -q .; then
  stamp=$(date +%Y%m%d_%H%M%S)
  docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    | gzip > "backups/pre_deploy_${stamp}.sql.gz"
  test -s "backups/pre_deploy_${stamp}.sql.gz"
  echo "backup: backups/pre_deploy_${stamp}.sql.gz"
else
  echo "БД ещё не запущена — backup пропущен (первичный деплой)."
fi

echo "== docker compose up -d --build =="
docker compose up -d --build

echo "== Миграции БД =="
docker compose run --rm api node dist/db/migrate.js

echo "== Готово. Проверь: https://mansband-kassa.ru =="
