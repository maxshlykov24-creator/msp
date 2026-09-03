#!/usr/bin/env bash
# Поднять только PO-token сервис для локального python3 bot.py
# Нужны: Docker Desktop / Docker Engine.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d pot
echo "Ожидание /ping..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
    echo "pot OK."
    echo "Добавь в .env строку: YT_POT_PROVIDER_URL=http://127.0.0.1:4416"
    echo "Затем перезапусти: python3 bot.py"
    exit 0
  fi
  sleep 1
done
echo "pot не ответил на ping — см. docker compose logs pot"
exit 1
