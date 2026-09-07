#!/usr/bin/env bash
# kb_search.sh — поиск по базе знаний компании (опц.)
#
# Вызывается агентом OpenClaw:
#   /opt/manager-bot/tools/kb_search.sh --query "сколько стоит окрашивание омбре"
#
# Простой grep-based поиск по всем .md в KB_PATH. Возвращает топ-3 совпадения.
# Для production — заменить на векторный поиск (embeddings) или OpenClaw memory module.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

QUERY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --query) QUERY="$2"; shift 2;;
    *) shift;;
  esac
done

if [ -z "${KB_PATH:-}" ] || [ ! -d "$KB_PATH" ]; then
  echo '{"ok": false, "error": "KB_PATH не задан или не существует — KB не подключена."}'
  exit 0
fi

if [ -z "$QUERY" ]; then
  echo '{"ok": false, "error": "Не задан --query"}'
  exit 1
fi

# Простейший поиск: grep по всем .md, top-3 файлов с наибольшим числом совпадений
RESULTS=$(grep -ril "$QUERY" "$KB_PATH" 2>/dev/null | head -3)

if [ -z "$RESULTS" ]; then
  echo "{\"ok\": true, \"matches\": [], \"message\": \"По запросу «${QUERY}» в базе знаний ничего не найдено\"}"
  exit 0
fi

# Собираем JSON через python (надёжнее чем jq для unknown encoding)
python3 -c "
import json, sys, os, re
files = '''$RESULTS'''.strip().split('\n')
query = '''$QUERY'''
matches = []
for path in files:
    if not path or not os.path.exists(path):
        continue
    try:
        with open(path, encoding='utf-8') as f:
            content = f.read()
    except OSError:
        continue
    # Найдём первое окружение запроса (±200 символов)
    idx = content.lower().find(query.lower())
    snippet = ''
    if idx >= 0:
        start = max(0, idx - 100)
        end = min(len(content), idx + len(query) + 200)
        snippet = content[start:end].replace('\n', ' ')
    matches.append({
        'file': os.path.relpath(path, '$KB_PATH'),
        'snippet': snippet,
    })
print(json.dumps({'ok': True, 'matches': matches[:3]}, ensure_ascii=False))
"

# Лог
if [ -n "${TOOLS_LOG_PATH:-}" ]; then
  mkdir -p "$(dirname "$TOOLS_LOG_PATH")"
  echo "{\"ts\":$(date +%s),\"tool\":\"kb_search\",\"query\":\"${QUERY}\"}" >> "$TOOLS_LOG_PATH"
fi
