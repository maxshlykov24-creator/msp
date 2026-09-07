#!/bin/sh
# Синхронизация по событию: агент закончил работу — коммитим и пушим.
#
# Зачем: раньше vault ждал тика launchd. Работа лежала незакоммиченной до 15 минут,
# а таймер молотил вхолостую даже когда ничего не менялось. Событие точнее таймера:
# закончил задачу — зафиксировал.
#
# Троттлинг касается только push: сеть дёргаем не чаще раза в 5 минут.
# Коммит делается всегда. Это принципиально: незакоммиченный слой — единственное,
# что реально теряется (03.09 `git stash --include-untracked` унёс 1235 файлов).
# Коммит стоит ~0.1-0.3 с и локален, экономить на нём нечего.
# Попали в окно троттлинга — работа уже в git, а флаг `.sync-pending` говорит
# таймеру, что осталось отправить.
#
# Отказ хука не должен мешать работе: любая проблема — выходим с 0.

cat >/dev/null 2>&1   # stdin с JSON события читаем и не используем

ok() { printf '{}\n'; exit 0; }

MIN_INTERVAL=300      # 5 минут

root=$(git rev-parse --show-toplevel 2>/dev/null) || ok
cd "$root" 2>/dev/null || ok

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-sync.log"
stamp_file="$log_dir/.last-event-sync"
pending_file="$log_dir/.sync-pending"
mkdir -p "$log_dir" 2>/dev/null

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file" 2>/dev/null; }

[ -f "$root/.vault-sync-off" ] && ok

branch=$(git symbolic-ref --short HEAD 2>/dev/null) || ok
[ "$branch" = "main" ] || ok

# Незавершённый rebase, merge или cherry-pick не трогаем
git_dir=$(git rev-parse --git-dir 2>/dev/null) || ok
for state in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD; do
  [ -e "$git_dir/$state" ] && ok
done

# Нечего отправлять: дерево чистое и локальных коммитов нет
if [ -z "$(git status --porcelain 2>/dev/null)" ] \
   && [ -z "$(git rev-list origin/main..HEAD 2>/dev/null)" ]; then
  rm -f "$pending_file" 2>/dev/null
  ok
fi

# Троттлинг по времени последней отправки
throttled=0
if [ -f "$stamp_file" ]; then
  last=$(cat "$stamp_file" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  waited=$(( $(date '+%s') - last ))
  [ "$waited" -lt "$MIN_INTERVAL" ] && throttled=1
fi

# В окне троттлинга: коммитим локально, отправку оставляем таймеру.
if [ "$throttled" = "1" ]; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    lock_dir="$log_dir/.sync.lock"
    if mkdir "$lock_dir" 2>/dev/null; then
      files=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
      git add -A >/dev/null 2>&1
      if git diff --cached --quiet 2>/dev/null; then
        :
      else
        GIT_SKIP_AUTO_PUSH=1 git commit -q -m "Работа агента: $files путей

Коммит хуком .cursor/hooks/vault-sync-stop.sh по завершении задачи.
Отправка отложена: с прошлой прошло ${waited}с из ${MIN_INTERVAL}с." >/dev/null 2>&1 \
          && log "stop-sync: закоммичено $files путей, отправка отложена (${waited}с из ${MIN_INTERVAL}с)"
      fi
      rmdir "$lock_dir" 2>/dev/null
    else
      log "stop-sync: лок занят, коммит отложен"
    fi
  fi
  touch "$pending_file" 2>/dev/null
  ok
fi

date '+%s' >"$stamp_file" 2>/dev/null
rm -f "$pending_file" 2>/dev/null

# Всю работу делает основной скрипт: там лок, ребейз, push и контроль живости.
# VAULT_SYNC_NOW=1 — не ждать тишины: правки только что закончились.
VAULT_SYNC_NOW=1 sh "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault-sync-auto.sh" >/dev/null 2>&1 \
  && log "stop-sync: ok" \
  || log "stop-sync: скрипт синхронизации вернул ошибку, см. записи выше"

# Синк мог не состояться: лок занят параллельным прогоном, конфликт, сеть.
# Тогда оставляем флаг, иначе работа ждала бы страховочного таймера полчаса.
if [ -n "$(git status --porcelain 2>/dev/null)" ] \
   || [ -n "$(git rev-list origin/main..HEAD 2>/dev/null)" ]; then
  touch "$pending_file" 2>/dev/null
  log "stop-sync: работа не ушла целиком — оставлен флаг для таймера"
fi

# Бэкап доступов раз в сутки. Живёт здесь, а не в launchd, потому что macOS
# не даёт процессам launchd писать в iCloud Drive: 07.09.2026 агент падал с
# `Operation not permitted`. Процесс хука наследует разрешения Cursor, и запись
# проходит. Раз в сутки решается по времени последнего архива.
secrets_dir="$HOME/Library/Mobile Documents/com~apple~CloudDocs/vault-secrets"
if [ -z "$(find "$secrets_dir" -name 'secrets-*.tar.gz.enc' -newermt '24 hours ago' 2>/dev/null | head -1)" ]; then
  sh "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/secrets-backup.sh" >/dev/null 2>&1 || true
fi

ok
