#!/bin/sh
# Вызов из корня репозитория: ./scripts/install-git-hooks.sh
# Рекомендуется на основном Mac (где чаще коммитишь). Настраивает core.hooksPath.

set -e
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

git config core.hooksPath scripts/githooks
chmod +x scripts/githooks/post-commit 2>/dev/null || true
echo "OK: core.hooksPath=scripts/githooks (post-commit будет пушить после коммита, если есть origin)."
