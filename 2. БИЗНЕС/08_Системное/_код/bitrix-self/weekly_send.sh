#!/bin/bash
# Понедельник, 09:00: сводка портала в Избранное рабочего Telegram.
set -euo pipefail
ROOT="/Users/max/CURSOR/2. БИЗНЕС/08_Системное/_код/bitrix-self"
TG="/Users/max/CURSOR/2. БИЗНЕС/08_Системное/_код/tg-self"
TEXT="$(/usr/bin/python3 "$ROOT/digest.py")"
if ! "$TG/.venv/bin/python" "$TG/tg.py" send-saved --account work "$TEXT"; then
  "$TG/.venv/bin/python" "$TG/tg.py" send-saved --account self "$TEXT"
fi
