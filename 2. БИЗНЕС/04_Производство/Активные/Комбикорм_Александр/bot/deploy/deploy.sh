#!/usr/bin/env bash
# Изолированный деплой комбикорм-бота на VPS.
# Запуск локально: SSH-доступ по ключу к root@HOST.
#   HOST=213.165.44.164 ./deploy/deploy.sh
#
# Создаёт user kombikorm, каталоги, venv, systemd-юнит. Соседние боты не трогает.
set -euo pipefail

HOST="${HOST:?укажите HOST=<ip>}"
SSH_KEY="${SSH_KEY:-}"
KEY_OPT=""
[ -n "$SSH_KEY" ] && KEY_OPT="-i $SSH_KEY"
SSH="ssh $KEY_OPT -o StrictHostKeyChecking=accept-new root@${HOST}"
RSH="ssh $KEY_OPT -o StrictHostKeyChecking=accept-new"
PROJ_LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> 1. Пользователь и каталоги"
$SSH bash -s <<'REMOTE'
set -e
id kombikorm >/dev/null 2>&1 || useradd --system --home /opt/kombikorm-bot --shell /usr/sbin/nologin kombikorm
mkdir -p /opt/kombikorm-bot /var/lib/kombikorm-bot/backups /var/log/kombikorm-bot
chown -R kombikorm:kombikorm /opt/kombikorm-bot /var/lib/kombikorm-bot /var/log/kombikorm-bot
REMOTE

echo ">>> 2. Синхронизация кода (без .env, .venv, data)"
rsync -az --delete -e "$RSH" \
  --exclude '.venv' --exclude 'data' --exclude '.env' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'catalog_review*' \
  "${PROJ_LOCAL}/" "root@${HOST}:/opt/kombikorm-bot/"

echo ">>> 3. venv + зависимости"
$SSH bash -s <<'REMOTE'
set -e
cd /opt/kombikorm-bot
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
chown -R kombikorm:kombikorm /opt/kombikorm-bot
REMOTE

echo ">>> 4. systemd-юнит"
$SSH bash -s <<'REMOTE'
set -e
cp /opt/kombikorm-bot/deploy/kombikorm-bot.service /etc/systemd/system/kombikorm-bot.service
systemctl daemon-reload
systemctl enable kombikorm-bot
REMOTE

echo ">>> Готово. Заполните /opt/kombikorm-bot/.env и: systemctl restart kombikorm-bot"
echo ">>> Логи: journalctl -u kombikorm-bot -f"
