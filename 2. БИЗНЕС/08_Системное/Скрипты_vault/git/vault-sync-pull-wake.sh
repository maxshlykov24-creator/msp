#!/bin/sh
# Pull при включении Mac / появлении сети (правки из Cursor Cloud → локальный vault).
# Из корня vault: 2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault-sync-pull-wake.sh

set -e

root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-sync.log"
conflict_file="$root/.vault-sync-conflict"
mkdir -p "$log_dir"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file"
}

notify() {
  title=$1
  message=$2
  osascript -e "display notification \"$message\" with title \"$title\"" 2>/dev/null || true
}

mark_conflict() {
  log "wake-pull: CONFLICT"
  touch "$conflict_file"
  notify "Vault sync" "Конфликт при wake-pull — нужен ручной разбор. См. vault-sync.log"
}

if [ -f "$root/.vault-sync-off" ]; then
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  exit 0
fi

branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
[ "$branch" = "main" ] || exit 0

git fetch origin 2>/dev/null || exit 0

# Если рабочее дерево "грязное" — не трогаем, авто-синк (commit→pull→push) разрулит сам.
# Иначе wake поднимал бы ложный конфликт на каждой правке.
if [ -n "$(git status --porcelain)" ]; then
  log "wake-pull: skip (dirty tree, auto-sync handles it)"
  exit 0
fi

# Нечего подтягивать — выходим тихо.
if [ -z "$(git rev-list HEAD..origin/main 2>/dev/null)" ]; then
  exit 0
fi

# Ребейз от origin/main, а не через pull: FETCH_HEAD общий на все git-процессы.
if git rebase origin/main >>"$log_file" 2>&1; then
  log "wake-pull: ok"
  rm -f "$conflict_file"
else
  git rebase --abort 2>/dev/null || true
  mark_conflict
  exit 1
fi
