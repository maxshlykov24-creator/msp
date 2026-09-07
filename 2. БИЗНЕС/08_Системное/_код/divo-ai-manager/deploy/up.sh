#!/usr/bin/env bash
# Выкат AI-менеджера на VPS. Запускать с мака из корня проекта:
#   deploy/up.sh
#
# Что делает: гасит бота, синхронизирует код и .env, ставит зависимости,
# ставит systemd-юнит, поднимает обратно, показывает лог.
set -euo pipefail

HOST="${DIVO_HOST:-root@104.171.136.226}"
KEY="${DIVO_KEY:-$HOME/.ssh/divo_deploy}"
REMOTE="${DIVO_REMOTE:-/root/divo-ai-manager}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

ssh_do() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" "$@"; }

echo "→ гашу бота (если запущен)"
ssh_do "systemctl stop divo-ai-bot 2>/dev/null || true"

echo "→ синхронизирую код в $REMOTE"
rsync -az --delete -e "ssh -i $KEY" \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '_ЭТАЛОН/' \
  --exclude 'workspace/state/' \
  --exclude 'workspace/paused/' \
  --exclude '.git/' \
  "$LOCAL/" "$HOST:$REMOTE/"

echo "→ .env (секреты отдельно, не под --delete)"
rsync -az -e "ssh -i $KEY" "$LOCAL/.env" "$HOST:$REMOTE/.env"
# Сервисный ключ Google живёт в divo-cme-stock, туда и смотрим.
ssh_do "grep -q '^GOOGLE_SA_PATH=/root' $REMOTE/.env || \
  sed -i 's|^GOOGLE_SA_PATH=.*|GOOGLE_SA_PATH=/root/divo-cme-stock/google_sa.json|' $REMOTE/.env; \
  grep -q '^GOOGLE_SA_PATH=' $REMOTE/.env || \
  echo 'GOOGLE_SA_PATH=/root/divo-cme-stock/google_sa.json' >> $REMOTE/.env; \
  chmod 600 $REMOTE/.env"

echo "→ venv и зависимости"
ssh_do "cd $REMOTE && (test -d .venv || python3 -m venv .venv) && \
  .venv/bin/pip -q install -U pip && .venv/bin/pip -q install -r requirements.txt"

echo "→ systemd"
ssh_do "install -m 644 $REMOTE/deploy/divo-proxy.service /etc/systemd/system/divo-proxy.service && \
  install -m 644 $REMOTE/deploy/divo-ai-bot.service /etc/systemd/system/divo-ai-bot.service && \
  systemctl daemon-reload && systemctl enable --now divo-proxy && systemctl enable --now divo-ai-bot"

echo "→ статус"
sleep 4
ssh_do "systemctl is-active divo-ai-bot; journalctl -u divo-ai-bot -n 20 --no-pager"
