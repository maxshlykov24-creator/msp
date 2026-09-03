#!/usr/bin/env bash
# Тэн (gstack) — установка инженерной команды в StartPack «Собери шестого»
# © 2026 Денис Леушин · Курс «Внедрение ИИ в бизнес» · Модуль 09
set -euo pipefail

REPO="https://github.com/garrytan/gstack.git"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gstack"

echo "🛠  Ставлю Тэн (gstack) — инженерную команду для сборки шестого…"

if ! command -v git >/dev/null 2>&1; then
  echo "❌ Нужен git. Поставь его и запусти скрипт снова."
  echo "   macOS:  xcode-select --install"
  echo "   Windows: https://git-scm.com/download/win"
  exit 1
fi

if [ -d "$DIR/.git" ]; then
  echo "↻  gstack уже стоит — обновляю…"
  git -C "$DIR" pull --ff-only || echo "⚠️  не смог обновить, оставляю как есть"
else
  echo "⬇️  Клонирую (shallow, без тяжёлой истории)…"
  git clone --depth 1 "$REPO" "$DIR"
fi

echo ""
echo "✅ Готово. Тэн лежит в: $DIR"
echo ""
echo "Дальше:"
echo "  • Методологию команд смотри в _ТЭН/КОМАНДЫ_ТЭН.md"
echo "  • Полный интерактивный/браузерный тулинг (нужен bun + Claude Code):"
echo "      cd \"$DIR\" && ./setup --prefix"
echo "  • На Cursor: открой нужный .md из gstack и попроси пройтись по его чек-листу для твоего агента."
echo ""
echo "Сначала фрейм (кого собираем) → потом Тэн (как собрать надёжно). Удачи со шестым 🎱"
