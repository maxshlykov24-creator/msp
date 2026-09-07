#!/bin/sh
# Авто-синхронизация vault → GitHub (commit → pull --rebase → push).
# Запуск: launchd каждые 2 мин или вручную из корня vault.
# Пауза: touch .vault-sync-off в корне vault

set -e

root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-sync.log"
lock_dir="$log_dir/.sync.lock"
conflict_file="$root/.vault-sync-conflict"
off_file="$root/.vault-sync-off"
had_conflict=0

mkdir -p "$log_dir"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file"
}

notify() {
  title=$1
  message=$2
  osascript -e "display notification \"$message\" with title \"$title\"" 2>/dev/null || true
}

# Удалить legacy-пути после незавершённой миграции (см. РЕГЛАМЕНТ_ЗОНЫ_БИЗНЕС.md)
for legacy in \
  "$root/Бизнес" \
  "$root/2. БИЗНЕС/04_Производство_внедрений" \
  "$root/2. БИЗНЕС/99_Системное"
do
  if [ -e "$legacy" ]; then
    rm -rf "$legacy"
    log "cleanup: removed legacy path $legacy"
  fi
done

mark_conflict() {
  log "CONFLICT: git pull --rebase failed — resolve manually, then rm .vault-sync-conflict"
  touch "$conflict_file"
  notify "Vault sync" "Конфликт — нужен ручной разбор. См. vault-sync.log"
}

release_lock() {
  rmdir "$lock_dir" 2>/dev/null || true
}

if ! mkdir "$lock_dir" 2>/dev/null; then
  log "skip: locked"
  exit 0
fi
trap release_lock EXIT

if [ -f "$off_file" ]; then
  log "skip: .vault-sync-off"
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  log "error: no origin remote"
  exit 1
fi

branch=$(git symbolic-ref --short HEAD 2>/dev/null) || {
  log "error: detached HEAD"
  exit 1
}

if [ "$branch" != "main" ]; then
  log "skip: branch=$branch (only main auto-syncs)"
  exit 0
fi

if [ -f "$conflict_file" ]; then
  had_conflict=1
fi

# Debounce перед commit: не трогать файлы, изменённые < 90 сек назад
if find . -type f \
  ! -path './.git/*' \
  ! -path './2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs/*' \
  -newermt '90 seconds ago' \
  2>/dev/null | grep -q .; then
  log "debounce: files changed in last 90s"
  exit 0
fi

git fetch origin 2>>"$log_file" || {
  log "error: git fetch failed"
  exit 1
}

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
  git add -A
  if git diff --cached --quiet; then
    log "ok: nothing to commit"
  else
    msg="vault sync $(date '+%Y-%m-%d %H:%M')"
    git commit -m "$msg" >>"$log_file" 2>&1
    log "commit: $msg"
  fi
fi

if ! git pull --rebase origin main >>"$log_file" 2>&1; then
  mark_conflict
  exit 1
fi

rm -f "$conflict_file"

if git push origin main >>"$log_file" 2>&1; then
  log "push: ok"
  if [ "$had_conflict" -eq 1 ]; then
    notify "Vault sync" "Синхронизация восстановлена"
  fi
else
  log "error: git push failed"
  exit 1
fi
