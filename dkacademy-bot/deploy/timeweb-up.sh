#!/usr/bin/env bash
# Старт на VPS: Docker Compose, API на 127.0.0.1:19082 (см. docker-compose.yml).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-dkacademy-bot}"

if ! command -v docker &>/dev/null; then
  echo "Нужны docker и docker compose plugin" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Создайте .env из .env.example и заполните секреты (amo, Telegram, LiveInform, field_id)." >&2
  exit 1
fi

echo ">>> Сборка и старт dkacademy-bot..."
docker compose up -d --build

echo ">>> Ожидание /health на 127.0.0.1:19082..."
for i in {1..45}; do
  if curl -sf "http://127.0.0.1:19082/health" >/dev/null 2>&1; then
    echo "OK: $(curl -s http://127.0.0.1:19082/health)"
    echo
    echo "Дальше: прокси HTTPS → 127.0.0.1:19082 (см. deploy/TIMEWEB.md и deploy/nginx/)."
    exit 0
  fi
  sleep 1
done
echo "Таймаут. Логи: docker compose logs -f api" >&2
exit 1
