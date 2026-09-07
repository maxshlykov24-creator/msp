#!/usr/bin/env bash
# Передача диалога живому менеджеру и возврат обратно.
# Использование:
#   tools/handoff.sh <chat_id>          — поставить диалог на паузу (агент молчит)
#   tools/handoff.sh --resume <chat_id> — вернуть диалог агенту
#   tools/handoff.sh --list             — показать все диалоги на паузе
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAUSED_DIR="${PAUSED_DIR:-$ROOT/workspace/paused}"
mkdir -p "$PAUSED_DIR"

case "${1:-}" in
  --list)
    ls -1 "$PAUSED_DIR" 2>/dev/null || true
    ;;
  --resume)
    chat_id="${2:?нужен chat_id}"
    rm -f "$PAUSED_DIR/$chat_id"
    echo "Диалог $chat_id возвращён агенту."
    ;;
  "" | -h | --help)
    sed -n '2,6p' "$0"
    exit 1
    ;;
  *)
    chat_id="$1"
    printf 'paused_at=%s\nreason=%s\n' "$(date -Iseconds)" "${2:-handoff}" > "$PAUSED_DIR/$chat_id"
    echo "Диалог $chat_id на паузе. Агент по нему молчит до --resume."
    ;;
esac
