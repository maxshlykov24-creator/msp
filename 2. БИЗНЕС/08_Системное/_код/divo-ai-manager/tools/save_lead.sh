#!/usr/bin/env bash
# save_lead.sh — сохранить полный диалог квалифицированного лида в файл
#
# Вызывается агентом OpenClaw:
#   /opt/manager-bot/tools/save_lead.sh \
#       --tg-id 123456789 \
#       --summary "ЗАПРОС: ..." \
#       --history-json '[{"role":"user","content":"..."},...]'
#
# Создаёт файл: workspace/_ВЫХОД/leads/YYYY-MM/<tg_id>_<timestamp>.md
# Возвращает JSON в stdout:
#   { "ok": true, "path": "workspace/_ВЫХОД/leads/2026-05/123_1715296800.md" }

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

WORKSPACE="${WORKSPACE_PATH:-$ROOT/workspace}"

TG_ID=""
SUMMARY=""
HISTORY_JSON="[]"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tg-id) TG_ID="$2"; shift 2;;
    --summary) SUMMARY="$2"; shift 2;;
    --history-json) HISTORY_JSON="$2"; shift 2;;
    *) shift;;
  esac
done

NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
NOW_TS=$(date -u +%s)
YEAR_MONTH=$(date -u +"%Y-%m")

OUT_DIR="${WORKSPACE}/_ВЫХОД/leads/${YEAR_MONTH}"
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR}/${TG_ID}_${NOW_TS}.md"

# Pretty-print history через python (чтобы получить читаемый md, а не сырой json)
PRETTY_HISTORY=$(python3 -c "
import json, sys
try:
    data = json.loads(sys.argv[1])
except json.JSONDecodeError:
    print('(история не парсится как JSON, raw):')
    print(sys.argv[1])
    sys.exit(0)
for msg in data:
    role = msg.get('role','?')
    icon = '👤' if role == 'user' else '🤖'
    content = msg.get('content','')
    print(f'**{icon} {role}:** {content}\n')
" "$HISTORY_JSON" 2>&1 || echo "(не удалось распарсить историю)")

cat > "$OUT_FILE" <<EOF
# Лид ${TG_ID} · ${NOW_ISO}

- **Tg ID:** \`${TG_ID}\`
- **Время:** ${NOW_ISO}
- **Агент:** lead-qualifier

## Резюме (LLM)

${SUMMARY}

## Полный диалог

${PRETTY_HISTORY}
EOF

# Лог
if [ -n "${TOOLS_LOG_PATH:-}" ]; then
  mkdir -p "$(dirname "$TOOLS_LOG_PATH")"
  echo "{\"ts\":${NOW_TS},\"tool\":\"save_lead\",\"tg_id\":\"${TG_ID}\",\"path\":\"${OUT_FILE}\"}" >> "$TOOLS_LOG_PATH"
fi

echo "{\"ok\": true, \"path\": \"${OUT_FILE}\"}"
