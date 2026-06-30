#!/usr/bin/env bash
# Поднять/обновить сервис аналитики. Запускать из каталога проекта на сервере.
#   cd /root/dkacademy-analytics && bash deploy/up.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Нет .env — скопируйте .env.example в .env и заполните (токен, AUTH_PASSWORD_HASH, SESSION_SECRET)." >&2
  exit 1
fi

docker compose up -d --build

echo "Жду health api…"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:19090/health >/dev/null 2>&1; then
    echo "OK: $(curl -fsS http://127.0.0.1:19090/health)"
    exit 0
  fi
  sleep 2
done
echo "health не поднялся за 60с — смотрите логи: docker compose logs --tail=100 api worker" >&2
exit 1
