#!/bin/sh
# Авто-синхронизация vault → GitHub (commit → rebase origin/main → push).
# Запуск: хук `stop` по завершении работы агента (основной путь), launchd раз в
# 15 мин как страховка для правок без агента, или вручную из корня vault.
# Пауза: touch .vault-sync-off в корне vault

set -e

root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-sync.log"
lock_dir="$log_dir/.sync.lock"
conflict_file="$root/.vault-sync-conflict"
off_file="$root/.vault-sync-off"
heartbeat_file="$log_dir/last-success"
debounce_file="$log_dir/.debounce-since"
# Флаг от хука `stop`: синк по событию попал в окно троттлинга (чаще раза в 5 мин)
# и переложил работу на этот прогон. При флаге ожидание тишины не применяем.
pending_file="$log_dir/.sync-pending"
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

# Legacy-пути после незавершённой миграции: только сигнал в лог.
# Удаление убрано 2026-09-07: скрипт делал rm -rf каждый прогон, без «да» и без
# переноса в 99_АРХИВ. Это нарушало запреты ядра и уносило данные молча.
for legacy in \
  "$root/Бизнес" \
  "$root/2. БИЗНЕС/04_Производство_внедрений" \
  "$root/2. БИЗНЕС/99_Системное"
do
  if [ -e "$legacy" ]; then
    log "warn: legacy path exists, разбери вручную: $legacy"
  fi
done

# Контроль живости. Молчаливая смерть синхронизации 07.07–13.08 прошла незамеченной,
# потому что «ничего не происходит» выглядит точно так же, как «всё хорошо».
if [ -f "$heartbeat_file" ] && [ -z "$(find "$heartbeat_file" -maxdepth 0 -newermt '24 hours ago' 2>/dev/null)" ]; then
  if [ ! -f "$log_dir/.stale-warned" ] || [ -z "$(find "$log_dir/.stale-warned" -maxdepth 0 -newermt '6 hours ago' 2>/dev/null)" ]; then
    log "warn: успешного push нет больше суток (последний: $(cat "$heartbeat_file" 2>/dev/null))"
    notify "Vault sync" "Сутки без успешной синхронизации. Смотри vault-sync.log"
    touch "$log_dir/.stale-warned"
  fi
else
  rm -f "$log_dir/.stale-warned" 2>/dev/null || true
fi

mark_conflict() {
  log "CONFLICT: git rebase origin/main failed — resolve manually, then rm .vault-sync-conflict"
  touch "$conflict_file"
  notify "Vault sync" "Конфликт — нужен ручной разбор. См. vault-sync.log"
}

release_lock() {
  rmdir "$lock_dir" 2>/dev/null || true
}

# Залипший лок. 2026-07-07 такой лок остался после падения прогона и молча
# заглушил синхронизацию на месяц: 9304 записи «skip: locked», последний push 06.07.
# Лок старше 10 минут считается мёртвым: дольше любого честного прогона, а окно
# молчания короче. Хук `stop` Cursor убивает по таймауту 120 с, и тогда trap не
# срабатывает — лок остаётся, и ждать полчаса незачем.
if [ -d "$lock_dir" ] && [ -z "$(find "$lock_dir" -maxdepth 0 -newermt '10 minutes ago' 2>/dev/null)" ]; then
  log "warn: снят залипший лок (старше 10 мин)"
  rmdir "$lock_dir" 2>/dev/null || true
fi

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

# Debounce перед commit: не трогать файлы, изменённые < 90 сек назад.
# Шумные пути исключены: watcher в node_modules или .venv держал бы debounce вечно.
# Ждать имеет смысл только если есть что коммитить. На чистом дереве ожидание
# откладывало бы ещё и приём правок со второго Mac.
#
# VAULT_SYNC_NOW=1 отключает ожидание: так зовёт хук `stop` по завершении работы
# агента. Там ждать тишины бессмысленно — правки только что закончились, и это
# ровно тот момент, когда работу надо зафиксировать.
if [ "${VAULT_SYNC_NOW:-0}" != "1" ] && [ ! -f "$pending_file" ] && [ -n "$(git status --porcelain)" ] && find . -type f \
  ! -path './.git/*' \
  ! -path './2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs/*' \
  ! -path '*/node_modules/*' \
  ! -path '*/.venv/*' \
  ! -path '*/__pycache__/*' \
  ! -path '*/.pytest_cache/*' \
  ! -path '*/dist/*' \
  ! -path '*/build/*' \
  ! -name '.DS_Store' \
  -newermt '90 seconds ago' \
  2>/dev/null | grep -q .; then
  # Потолок ожидания. Без него непрерывная правка файлов заглушила бы синхронизацию
  # так же молча, как залипший лок 07.07: каждый прогон честно ждёт, push не идёт никогда.
  if [ ! -f "$debounce_file" ]; then
    date '+%s' >"$debounce_file"
  fi
  since=$(cat "$debounce_file" 2>/dev/null || echo 0)
  waited=$(( $(date '+%s') - since ))
  if [ "$waited" -lt 1800 ]; then
    log "debounce: files changed in last 90s (ждём $waited сек из 1800)"
    exit 0
  fi
  log "warn: debounce держится больше 30 мин — коммитим не дожидаясь тишины"
fi
rm -f "$debounce_file" 2>/dev/null || true

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

# Ребейз от remote-tracking, а не через pull.
# `git pull --rebase origin main` читает ветку для слияния из общего .git/FETCH_HEAD.
# 2026-09-07 18:15 и 18:30 это дало `fatal: Cannot rebase onto multiple branches`:
# параллельный git-процесс перезаписал FETCH_HEAD между fetch и pull.
# `origin/main` — обычная ссылка, гонке не подвержена.
# Вывод fetch держим в переменной и пишем только при ошибке: холостых прогонов
# ~96 в сутки, и каждый дописывал бы «* branch main -> FETCH_HEAD» в лог.
if ! fetch_out=$(git fetch origin main 2>&1); then
  log "error: git fetch origin main failed"
  printf '%s\n' "$fetch_out" >>"$log_file"
  exit 1
fi

# Холостой прогон: делать нечего. Выходим до push, но heartbeat обновляем —
# fetch выше уже подтвердил, что GitHub на связи, а состояние совпадает.
# Без этого сутки без правок дали бы ложный алерт «синхронизация мертва».
# В лог не пишем: холостых прогонов много, лог и так вырос до 18 тысяч строк.
if [ ! -f "$conflict_file" ] \
   && [ -z "$(git status --porcelain)" ] \
   && [ -z "$(git rev-list origin/main..HEAD 2>/dev/null)" ] \
   && [ -z "$(git rev-list HEAD..origin/main 2>/dev/null)" ]; then
  date '+%Y-%m-%d %H:%M:%S' >"$heartbeat_file"
  rm -f "$pending_file" 2>/dev/null || true
  exit 0
fi

if ! git rebase origin/main >>"$log_file" 2>&1; then
  git rebase --abort 2>/dev/null || true
  mark_conflict
  exit 1
fi

rm -f "$conflict_file"

if git push origin main >>"$log_file" 2>&1; then
  log "push: ok"
  date '+%Y-%m-%d %H:%M:%S' >"$heartbeat_file"
  rm -f "$pending_file" 2>/dev/null || true
  if [ "$had_conflict" -eq 1 ]; then
    notify "Vault sync" "Синхронизация восстановлена"
  fi
else
  log "error: git push failed"
  exit 1
fi
