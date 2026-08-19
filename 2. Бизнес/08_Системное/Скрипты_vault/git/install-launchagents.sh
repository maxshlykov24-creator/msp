#!/bin/sh
# Установить launchd-агенты авто-синхронизации vault.
# Из корня vault: bash Бизнес/99_Системное/Скрипты_vault/git/install-launchagents.sh

set -e
root=$(cd "$(dirname "$0")/../../../.." && pwd)
git_dir="$root/Бизнес/99_Системное/Скрипты_vault/git"
agents="$HOME/Library/LaunchAgents"

chmod +x "$git_dir/vault-sync-auto.sh" "$git_dir/vault-sync-pull-wake.sh" "$git_dir/vault-sync-pull.sh" 2>/dev/null || true

for label in com.msp.vault-sync com.msp.vault-sync-wake; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
done

cp "$git_dir/com.msp.vault-sync.plist" "$agents/"
cp "$git_dir/com.msp.vault-sync-wake.plist" "$agents/"

launchctl bootstrap "gui/$(id -u)" "$agents/com.msp.vault-sync.plist"
launchctl bootstrap "gui/$(id -u)" "$agents/com.msp.vault-sync-wake.plist"

echo "OK: launchd agents installed (sync every 2 min, wake pull every 5 min + at login)"
