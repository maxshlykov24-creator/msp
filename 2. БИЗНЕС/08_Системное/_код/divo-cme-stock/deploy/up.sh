#!/usr/bin/env bash
# Выкладка на VPS DIVO. Запускать локально из корня сервиса:
#   bash deploy/up.sh
set -euo pipefail

HOST="${DIVO_HOST:-root@104.171.136.226}"
KEY="${DIVO_SSH_KEY:-$HOME/.ssh/divo_deploy}"
REMOTE=/root/divo-cme-stock
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$HOST")

echo "── rsync → $HOST:$REMOTE"
"${SSH[@]}" "mkdir -p $REMOTE"
rsync -az --delete \
  -e "ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" \
  --exclude '.env' --exclude '__pycache__' --exclude '.venv' \
  --exclude 'data' --exclude '.pytest_cache' --exclude 'google_sa.json' \
  "$DIR/app" "$DIR/Dockerfile" "$DIR/docker-compose.yml" \
  "$DIR/requirements.txt" "$DIR/.env.example" "$DIR/deploy" \
  "$HOST:$REMOTE/"

if [[ -f "$DIR/google_sa.json" ]]; then
  echo "── google_sa.json"
  scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
    "$DIR/google_sa.json" "$HOST:$REMOTE/google_sa.json"
fi

"${SSH[@]}" "test -f $REMOTE/.env" || {
  echo "создаю $REMOTE/.env из примера (дописать CME_CLIENT_ID/SECRET и Telegram)"
  "${SSH[@]}" "cp $REMOTE/.env.example $REMOTE/.env"
}

echo "── docker compose up"
"${SSH[@]}" "cd $REMOTE && docker compose up -d --build"
"${SSH[@]}" "cd $REMOTE && docker compose ps"
echo "логи: ssh -i $KEY $HOST 'cd $REMOTE && docker compose logs --tail=80 worker'"
