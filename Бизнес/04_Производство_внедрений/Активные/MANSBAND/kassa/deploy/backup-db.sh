#!/usr/bin/env bash
# Бэкап БД кассы (pg_dump из контейнера) + ротация. Ставится в cron на VPS.
# crontab:  0 3 * * * /opt/mansband-kassa/deploy/backup-db.sh >> /var/log/kassa-backup.log 2>&1
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/mansband-kassa}"
BACKUP_DIR="${BACKUP_DIR:-/opt/mansband-kassa/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
cd "$REMOTE_DIR"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d_%H-%M)"
OUT="${BACKUP_DIR}/kassa_${STAMP}.sql.gz"

# Логин/база берём из .env
set -a; . "${REMOTE_DIR}/.env"; set +a
PGUSER="${POSTGRES_USER:-kassa}"
PGDB="${POSTGRES_DB:-kassa}"

docker compose exec -T db pg_dump -U "$PGUSER" "$PGDB" | gzip > "$OUT"
echo "Бэкап: $OUT"

# Ротация
find "$BACKUP_DIR" -name 'kassa_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "Старые бэкапы (>${KEEP_DAYS} дн.) удалены."
