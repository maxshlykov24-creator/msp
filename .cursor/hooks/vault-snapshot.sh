#!/bin/sh
# UI hook: JSON success is not proof of Git delivery; inspect sync log/status.
cat >/dev/null 2>&1
root=$(git rev-parse --show-toplevel 2>/dev/null) || { printf "{}\n"; exit 0; }
cd "$root" || { printf "{}\n"; exit 0; }
python3 "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault_sync.py" snapshot >/dev/null 2>&1
printf "{}\n"
exit 0
