#!/bin/sh
# Для второго Mac: вызывай перед работой в этом репозитории (или по ярлыку).
# Из корня vault: 2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault-sync-pull.sh

set -e
root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Нет remote origin. Добавь: git remote add origin <url>" >&2
  exit 1
fi

branch=$(git symbolic-ref --short HEAD 2>/dev/null) || {
  echo "Не удалось определить ветку." >&2
  exit 1
}

git fetch origin
git rebase "origin/$branch"
