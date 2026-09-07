#!/usr/bin/env bash
# Восстановление БД аналитики из бэкапа.
#   bash deploy/restore.sh backups/divoanalytics_20260716_030000.sql.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="${1:?Укажите путь к .sql.gz: bash deploy/restore.sh backups/divoanalytics_YYYYMMDD_HHMMSS.sql.gz}"
[[ -f "$FILE" ]] || { echo "Файл не найден: $FILE" >&2; exit 1; }

cd "$ROOT"
echo "Останавливаю api/worker, чтобы не писали во время восстановления…"
docker compose stop api worker

echo "Пересоздаю БД divoanalytics…"
docker compose exec -T db psql -U divoanalytics -d postgres -c "DROP DATABASE IF EXISTS divoanalytics;"
docker compose exec -T db psql -U divoanalytics -d postgres -c "CREATE DATABASE divoanalytics OWNER divoanalytics;"

echo "Восстанавливаю из ${FILE}…"
gunzip -c "$FILE" | docker compose exec -T db psql -U divoanalytics -d divoanalytics

echo "Запускаю api/worker…"
docker compose start api worker
echo "Готово. Проверка: curl -fsS http://127.0.0.1:19100/health"
