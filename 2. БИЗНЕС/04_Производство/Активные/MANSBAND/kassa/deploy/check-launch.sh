#!/usr/bin/env bash
# Предзапусковая проверка кассы на VPS (созвон 04.09, п.8).
# Запуск на сервере:  bash /opt/mansband-kassa/deploy/check-launch.sh
# Ничего не меняет — только печатает состояние и возвращает 1, если есть провалы.

REMOTE_DIR="${REMOTE_DIR:-/opt/mansband-kassa}"
BACKUP_DIR="${BACKUP_DIR:-${REMOTE_DIR}/backups}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:3001/api/health}"

fails=0
ok()   { printf '  ok   %s\n' "$1"; }
warn() { printf '  ?    %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }

echo "Касса MANSBAND: проверка перед запуском"
cd "$REMOTE_DIR" 2>/dev/null || { bad "нет каталога $REMOTE_DIR"; exit 1; }

echo "Контейнеры"
if docker compose ps --status running --services 2>/dev/null | grep -q .; then
  for svc in db api web; do
    if docker compose ps --status running --services 2>/dev/null | grep -qx "$svc"; then
      ok "$svc поднят"
    else
      bad "$svc не запущен"
    fi
  done
else
  bad "docker compose не отвечает или всё лежит"
fi

echo "API"
if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
  ok "$HEALTH_URL отвечает"
else
  bad "$HEALTH_URL не отвечает"
fi

echo "Бэкапы"
if crontab -l 2>/dev/null | grep -q 'backup-db.sh'; then
  ok "cron бэкапа прописан"
else
  bad "cron бэкапа не найден: 0 3 * * * ${REMOTE_DIR}/deploy/backup-db.sh >> /var/log/kassa-backup.log 2>&1"
fi
latest="$(find "$BACKUP_DIR" -name 'kassa_*.sql.gz' -mtime -2 2>/dev/null | sort | tail -1)"
if [ -n "$latest" ]; then
  ok "свежий дамп: $latest ($(du -h "$latest" | cut -f1))"
else
  bad "нет дампа за последние 2 суток в $BACKUP_DIR"
fi

echo "Секреты"
if [ -f "${REMOTE_DIR}/.env" ]; then
  perms="$(stat -c '%a' "${REMOTE_DIR}/.env" 2>/dev/null || echo '?')"
  [ "$perms" = "600" ] && ok ".env с правами 600" || warn ".env с правами $perms, лучше chmod 600"
  # Токены amoCRM и МойСклад светились в переписке — после запуска обязателен перевыпуск.
  for key in AMOCRM_LONG_LIVED_TOKEN MOYSKLAD_API_TOKEN JWT_SECRET WEBHOOK_SECRET POSTGRES_PASSWORD; do
    val="$(grep -E "^${key}=" "${REMOTE_DIR}/.env" | head -1 | cut -d= -f2- | tr -d '"')"
    if [ -z "$val" ]; then
      bad "$key пуст"
    elif printf '%s' "$val" | grep -qiE 'ЗАМЕНИ|ВСТАВЬ|change|example|todo'; then
      bad "$key остался заглушкой из .env.example"
    else
      ok "$key задан"
    fi
  done
else
  bad "нет ${REMOTE_DIR}/.env"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "Готово: провалов нет."
else
  echo "Провалов: $fails — до запуска закрыть."
fi
exit $((fails > 0))
