#!/usr/bin/env bash
# Однократная нативная установка на Ubuntu (без Docker), порт 18080.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql postgresql-contrib python3.12-venv python3.12-dev build-essential libpq-dev

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='loyalty'" 2>/dev/null | grep -q 1 \
  || sudo -u postgres psql -c "CREATE USER loyalty WITH PASSWORD 'loyaltypg';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'loyalty'" 2>/dev/null | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE loyalty OWNER loyalty;"

ROOT="/root/loyalty-service"
cd "$ROOT"
sed -i 's|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg2://loyalty:loyaltypg@127.0.0.1:5432/loyalty|' .env

python3.12 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q -r requirements.txt
python -c "from app.config import get_settings; get_settings.cache_clear(); from app.database import init_db; init_db()"

cat > /etc/systemd/system/loyalty.service <<'UNIT'
[Unit]
Description=MartaChe Loyalty Service
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/loyalty-service
EnvironmentFile=/root/loyalty-service/.env
ExecStart=/root/loyalty-service/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18080
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable loyalty.service
systemctl restart loyalty.service
sleep 2
systemctl --no-pager status loyalty.service || true
curl -sf http://127.0.0.1:18080/health && echo " health OK"
