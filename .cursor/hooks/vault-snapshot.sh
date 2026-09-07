#!/bin/sh
# Снапшот незакоммиченной работы перед задачей агента.
#
# Зачем: Cursor перед запуском облачного агента делает `git stash --include-untracked`.
# 2026-09-03 так ушли 1235 файлов, накопленных за две недели без коммитов.
# Если дерево чистое, стэшу нечего забирать.
#
# Событие: beforeSubmitPrompt. Push не делаем — промпт не должен ждать сеть,
# отправку берёт на себя launchd-агент com.msp.vault-sync (каждые 15 мин).
#
# Отказ хука не должен мешать работе: любая проблема — выходим с 0 и пропускаем снапшот.

cat >/dev/null 2>&1   # stdin с JSON события нам не нужен, но прочитать надо

ok() { printf '{}\n'; exit 0; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || ok
cd "$root" 2>/dev/null || ok

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/vault-snapshot.log"
lock_dir="$log_dir/.sync.lock"   # тот же лок, что у launchd: не бежим одновременно
mkdir -p "$log_dir" 2>/dev/null

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file" 2>/dev/null; }

# Пауза синхронизации уважается и здесь
[ -f "$root/.vault-sync-off" ] && ok

# Только main: на других ветках снапшот смешал бы работу
branch=$(git symbolic-ref --short HEAD 2>/dev/null) || ok
[ "$branch" = "main" ] || ok

# Незавершённый rebase, merge или cherry-pick не трогаем
git_dir=$(git rev-parse --git-dir 2>/dev/null) || ok
for state in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD; do
  if [ -e "$git_dir/$state" ]; then
    log "skip: незавершённая операция git ($state)"
    ok
  fi
done

# Дерево чистое — делать нечего
[ -z "$(git status --porcelain 2>/dev/null)" ] && ok

# Залипший лок старше 10 минут — мёртвый: снимаем, иначе снапшоты молча перестанут идти
if [ -d "$lock_dir" ] && [ -z "$(find "$lock_dir" -maxdepth 0 -newermt '10 minutes ago' 2>/dev/null)" ]; then
  log "warn: снят залипший лок (старше 10 мин)"
  rmdir "$lock_dir" 2>/dev/null || true
fi

if ! mkdir "$lock_dir" 2>/dev/null; then
  log "skip: занято автосинком"
  ok
fi

files=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
git add -A >/dev/null 2>&1

body="Автоматический коммит хуком .cursor/hooks/vault-snapshot.sh.
Смысл: не отдавать незакоммиченную работу в git stash при запуске агента."

# Cursor вызывает beforeSubmitPrompt перед каждым шагом агента, а не раз на задачу.
# Поэтому длинная задача давала бы десяток коммитов «Снапшот: 1 путей».
# Если предыдущий снапшот ещё не ушёл на origin — дополняем его, а не плодим новые.
amend=""
last=$(git log -1 --format=%s 2>/dev/null)
case "$last" in
  "Снапшот работы агента:"*)
    if [ -n "$(git rev-list origin/main..HEAD 2>/dev/null)" ]; then
      amend="--amend"
    fi
    ;;
esac

if git diff --cached --quiet 2>/dev/null; then
  log "ok: нечего коммитить"
elif GIT_SKIP_AUTO_PUSH=1 git commit -q $amend -m "Снапшот работы агента

$body" >/dev/null 2>&1; then
  # Число путей известно только по готовому коммиту (при --amend он объединён с прежним)
  files=$(git show --name-only --format= HEAD 2>/dev/null | grep -c .)
  GIT_SKIP_AUTO_PUSH=1 git commit -q --amend -m "Снапшот работы агента: $files путей

$body" >/dev/null 2>&1
  log "снапшот${amend:+ (дополнен)}: $files путей, $(git rev-parse --short HEAD)"
else
  log "error: коммит не удался"
fi

rmdir "$lock_dir" 2>/dev/null
ok
