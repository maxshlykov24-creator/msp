#!/bin/sh
# Авто-синхронизация vault → GitHub (pull → commit → push).
# Запуск: launchd каждые 2 мин или вручную из корня vault.
# Пауза: touch .vault-sync-off в корне vault

set -e

root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

log_dir="$root/Бизнес/99_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-sync.log"
conflict_file="$root/.vault-sync-conflict"
off_file="$root/.vault-sync-off"

mkdir -p "$log_dir"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file"
}

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

git fetch origin 2>>"$log_file" || {
  log "error: git fetch failed"
  exit 1
}

if ! git pull --rebase --autostash origin main >>"$log_file" 2>&1; then
  log "CONFLICT: git pull --rebase failed — resolve manually, then rm .vault-sync-conflict"
  touch "$conflict_file"
  exit 1
fi

rm -f "$conflict_file"

# Debounce перед commit: не трогать файлы, изменённые < 90 сек назад
if find . -type f \
  ! -path './.git/*' \
  ! -path './Бизнес/99_Системное/Скрипты_vault/git/logs/*' \
  -newermt '90 seconds ago' \
  2>/dev/null | grep -q .; then
  log "debounce: files changed in last 90s"
  exit 0
fi

if git diff --quiet && git diff --cached --quiet; then
  # nothing to commit after pull
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
  fi
fi

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
  git add -A
  if git diff --cached --quiet; then
    log "ok: nothing to commit"
    exit 0
  fi
  msg="vault sync $(date '+%Y-%m-%d %H:%M')"
  git commit -m "$msg" >>"$log_file" 2>&1
  log "commit: $msg"
fi

if git push origin main >>"$log_file" 2>&1; then
  log "push: ok"
else
  log "error: git push failed"
  exit 1
fi
