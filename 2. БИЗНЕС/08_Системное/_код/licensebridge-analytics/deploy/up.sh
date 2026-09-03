#!/usr/bin/env bash
# Выкладка на VPS хаба (72.56.123.137). Запускать локально из корня сервиса.
#   bash deploy/up.sh            — синхронизировать код и пересобрать
#   bash deploy/up.sh --collect  — то же + принудительный пересбор среза
set -euo pipefail

HOST=${LB_ANALYTICS_HOST:-licensebridge-hub}
REMOTE=/opt/licensebridge-analytics
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "── синхронизация кода → $HOST:$REMOTE"
ssh "$HOST" "mkdir -p $REMOTE"
# .env остаётся на сервере: токен Kommo и хеш пароля в репозиторий не попадают
rsync -az --delete \
    --exclude '.env' --exclude '__pycache__' --exclude '.venv' \
    "$DIR/app" "$DIR/web" "$DIR/Dockerfile" "$DIR/docker-compose.yml" \
    "$DIR/requirements.txt" "$DIR/build_web.py" "$DIR/deploy" \
    "$HOST:$REMOTE/"

ssh "$HOST" "test -f $REMOTE/.env" || {
    echo "НЕТ $REMOTE/.env — создайте его по .env.example (токен Kommo, хеш пароля, SESSION_SECRET)"
    exit 1
}

echo "── сборка и запуск"
ssh "$HOST" "cd $REMOTE && docker compose up -d --build"

echo "── ожидание health"
for _ in $(seq 1 30); do
    if ssh "$HOST" "docker exec lb-analytics curl -fsS http://127.0.0.1:8080/health" 2>/dev/null; then
        echo
        break
    fi
    sleep 5
done

if [[ "${1:-}" == "--collect" ]]; then
    echo "── принудительный сбор среза"
    ssh "$HOST" "docker exec lb-analytics python -c 'from app.collector import run_collection; print(run_collection())'"
fi

echo "── Caddy (домен обслуживает хаб)"
ssh "$HOST" "docker exec lb-hub-caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -2 || true"
echo "готово: https://lb.mspod24.ru"
