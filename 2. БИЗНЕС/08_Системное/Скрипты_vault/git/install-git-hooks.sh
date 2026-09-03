#!/bin/sh
# Вызов из корня vault: 2. БИЗНЕС/08_Системное/Скрипты_vault/git/install-git-hooks.sh
# Рекомендуется на основном Mac (где чаще коммитишь). Настраивает core.hooksPath.

set -e
root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"
hooks="2. БИЗНЕС/08_Системное/Скрипты_vault/git/githooks"

git config core.hooksPath "$hooks"
chmod +x "$hooks/post-commit" 2>/dev/null || true
echo "OK: core.hooksPath=$hooks (post-commit будет пушить после коммита, если есть origin)."
