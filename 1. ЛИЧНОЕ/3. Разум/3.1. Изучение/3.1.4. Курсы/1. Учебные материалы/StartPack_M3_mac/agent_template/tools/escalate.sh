#!/usr/bin/env bash
# escalate.sh — передать квалифицированного лида в TG-чат менеджера
#
# Вызывается агентом OpenClaw:
#   /opt/manager-bot/tools/escalate.sh \
#       --tg-id 123456789 \
#       --reason ready_qualified \
#       --summary "ЗАПРОС: внедрение AI..."
#
# Возвращает JSON в stdout:
#   { "ok": true, "manager_chat_id": "...", "tg_message_id": 42 }
# или
#   { "ok": false, "error": "..." }

set -euo pipefail

# Загрузить .env из корня проекта (на 1 уровень выше tools/)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

# Парсим аргументы
TG_ID=""
REASON=""
SUMMARY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tg-id) TG_ID="$2"; shift 2;;
    --reason) REASON="$2"; shift 2;;
    --summary) SUMMARY="$2"; shift 2;;
    *) shift;;
  esac
done

if [ -z "${MANAGER_CHAT_ID:-}" ] || [ -z "${TG_BOT_TOKEN_LEAD_QUAL:-}" ]; then
  echo '{"ok": false, "error": "MANAGER_CHAT_ID или TG_BOT_TOKEN_LEAD_QUAL не задан в .env"}'
  exit 1
fi

# Telegram-сообщение менеджеру
TEXT=$(cat <<EOF
🟢 <b>Новый квалифицированный лид</b> · агент: lead-qualifier

<b>От Tg ID:</b> <code>${TG_ID}</code>
<b>Причина передачи:</b> ${REASON}

<b>Резюме:</b>
${SUMMARY}

<b>Связаться:</b> <a href="tg://user?id=${TG_ID}">открыть чат</a>
EOF
)

RESPONSE=$(curl -s -X POST \
  "https://api.telegram.org/bot${TG_BOT_TOKEN_LEAD_QUAL}/sendMessage" \
  -d "chat_id=${MANAGER_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  -d "parse_mode=HTML" \
  -d "disable_web_page_preview=true")

OK=$(echo "$RESPONSE" | grep -o '"ok":true' || true)

if [ -n "$OK" ]; then
  MSG_ID=$(echo "$RESPONSE" | grep -o '"message_id":[0-9]*' | head -1 | cut -d: -f2)

  # Лог для аудита
  if [ -n "${TOOLS_LOG_PATH:-}" ]; then
    mkdir -p "$(dirname "$TOOLS_LOG_PATH")"
    echo "{\"ts\":$(date +%s),\"tool\":\"escalate\",\"tg_id\":\"${TG_ID}\",\"reason\":\"${REASON}\",\"ok\":true}" >> "$TOOLS_LOG_PATH"
  fi

  echo "{\"ok\": true, \"manager_chat_id\": \"${MANAGER_CHAT_ID}\", \"tg_message_id\": ${MSG_ID}}"
else
  ERROR=$(echo "$RESPONSE" | grep -o '"description":"[^"]*"' | cut -d: -f2-)
  echo "{\"ok\": false, \"error\": ${ERROR:-\"\\\"unknown\\\"\"}}"
  exit 2
fi
