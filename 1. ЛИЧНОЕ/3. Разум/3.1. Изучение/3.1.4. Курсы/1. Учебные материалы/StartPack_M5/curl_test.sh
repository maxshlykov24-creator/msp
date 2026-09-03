#!/usr/bin/env bash
# Ручной тест без CRM: шлём вебхук с прямой ссылкой на запись звонка.
# Сервис скачает запись, расшифрует и пришлёт транскрипт в Telegram.
#
# Использование:
#   bash curl_test.sh "https://example.com/zvonok.mp3"
#   bash curl_test.sh "https://example.com/zvonok.mp3" "http://localhost:8000"
#
# Ссылка должна быть прямой (заканчиваться на файл) и доступной с этого сервера.

set -euo pipefail

AUDIO_URL="${1:-}"
BASE="${2:-http://localhost:8000}"

if [[ -z "$AUDIO_URL" ]]; then
  echo "Укажи ссылку на запись: bash curl_test.sh <URL_аудио> [base_url]" >&2
  exit 1
fi

curl -fsS -X POST "$BASE/webhook/amo-call" \
  -H "Content-Type: application/json" \
  -d "{\"link\": \"$AUDIO_URL\", \"phone\": \"+7 900 000-00-00\", \"duration\": 0}"

echo
echo "Принято. Транскрипт придёт в Telegram через ~30–90 сек (смотри логи сервиса)."
