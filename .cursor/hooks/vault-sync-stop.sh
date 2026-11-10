#!/bin/sh
# UI hook: JSON success is not proof of Git delivery; inspect sync log/status.
cat >/dev/null 2>&1
root=$(git rev-parse --show-toplevel 2>/dev/null) || { printf "{}\n"; exit 0; }
cd "$root" || { printf "{}\n"; exit 0; }
python3 "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault_sync.py" stop >/dev/null 2>&1

if [ ! -f "$root/.vault-sync-off" ]; then
# Бэкап доступов раз в сутки. Живёт здесь, а не в launchd, потому что macOS
# не даёт процессам launchd писать в iCloud Drive: 07.09.2026 агент падал с
# `Operation not permitted`. Процесс хука наследует разрешения Cursor, и запись
# проходит. Раз в сутки решается по времени последнего архива.
secrets_dir="$HOME/Library/Mobile Documents/com~apple~CloudDocs/vault-secrets"
if [ -z "$(find "$secrets_dir" -name 'secrets-*.tar.gz.enc' -newermt '24 hours ago' 2>/dev/null | head -1)" ]; then
  sh "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/secrets-backup.sh" >/dev/null 2>&1 || true
fi

fi
printf "{}\n"
exit 0
