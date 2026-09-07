#!/bin/sh
# Синхронизация по событию: агент закончил работу — коммитим и пушим.
#
# Зачем: раньше vault ждал тика launchd. Работа лежала незакоммиченной до 15 минут,
# а таймер молотил вхолостую даже когда ничего не менялось. Событие точнее таймера:
# закончил задачу — зафиксировал.
#
# Троттлинг: не чаще одного синка в 5 минут. Если попали в окно, ставим флаг
# `.sync-pending` — таймер (раз в 30 мин) подхватит и досинхронизирует.
# Без флага частые короткие задачи копили бы незакоммиченную работу молча.
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

# Троттлинг по времени последнего события
if [ -f "$stamp_file" ]; then
  last=$(cat "$stamp_file" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  waited=$(( $(date '+%s') - last ))
  if [ "$waited" -lt "$MIN_INTERVAL" ]; then
    touch "$pending_file" 2>/dev/null
    log "stop-sync: отложен, прошло ${waited}с из ${MIN_INTERVAL}с — оставлен флаг для таймера"
    ok
  fi
fi

date '+%s' >"$stamp_file" 2>/dev/null
rm -f "$pending_file" 2>/dev/null

# Всю работу делает основной скрипт: там лок, ребейз, push и контроль живости.
# VAULT_SYNC_NOW=1 — не ждать тишины: правки только что закончились.
VAULT_SYNC_NOW=1 sh "$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault-sync-auto.sh" >/dev/null 2>&1 \
  && log "stop-sync: ok" \
  || log "stop-sync: скрипт синхронизации вернул ошибку, см. записи выше"

ok
