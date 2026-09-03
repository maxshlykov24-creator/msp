#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-loyalty-service}"

if ! command -v docker &>/dev/null; then
  echo "Нужен docker и docker compose" >&2
  exit 1
fi

echo ">>> Сборка и старт (Timeweb / same host)..."
docker compose -f docker-compose.timeweb.yml up -d --build

echo ">>> Ожидание /health на 127.0.0.1:18080..."
for i in {1..30}; do
  if curl -sf "http://127.0.0.1:18080/health" >/dev/null 2>&1; then
    echo "OK: $(curl -s http://127.0.0.1:18080/health)"
    echo
    echo "Дальше: в nginx на этом сервере добавь location из deploy/nginx/loyalty-same-host.example.conf"
    echo "Потом: python scripts/register_webhooks.py (с MS_TOKEN в .env)"
    exit 0
  fi
  sleep 1
done
echo "Таймаут: проверь логи: docker compose -f docker-compose.timeweb.yml logs -f api" >&2
exit 1
