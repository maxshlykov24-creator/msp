#!/usr/bin/env bash
# Ежедневный бэкап БД аналитики. Поставить в cron:
#   0 3 * * * /root/dkacademy-analytics/deploy/backup.sh >> /var/log/dka-backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT}/backups"
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${BACKUP_DIR}/dkanalytics_${STAMP}.sql.gz"

docker compose -f "${ROOT}/docker-compose.yml" exec -T db \
  pg_dump -U dkanalytics dkanalytics | gzip > "$OUT"

echo "$(date -Is) backup -> ${OUT} ($(du -h "$OUT" | cut -f1))"

# хранить только 14 последних
ls -1t "${BACKUP_DIR}"/dkanalytics_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
