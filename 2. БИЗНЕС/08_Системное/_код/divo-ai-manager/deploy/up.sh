#!/usr/bin/env bash
# Выкат AI-менеджера на VPS. Запускать с мака из корня проекта:
#   deploy/up.sh
#
# Что делает: проверяет, что выкатывать нечего кроме закоммиченного, гасит
# бота, синхронизирует код и .env, ставит зависимости и systemd-юнит,
# поднимает обратно, показывает лог.
#
# Обход проверок (осознанно, на свой риск): DIVO_SKIP_GUARD=1 deploy/up.sh
set -euo pipefail

HOST="${DIVO_HOST:-root@104.171.136.226}"
KEY="${DIVO_KEY:-$HOME/.ssh/divo_deploy}"
REMOTE="${DIVO_REMOTE:-/root/divo-ai-manager}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

EXCLUDES=(
  --exclude '.venv/'
  --exclude '__pycache__/'
  --exclude '_ЭТАЛОН/'
  --exclude 'workspace/state/'
  --exclude 'workspace/paused/'
  # Сток на проде генерирует сам бот каждые 15 минут. Копия с мака почти
  # всегда старее, незачем откатывать наличие машин выкатом.
  --exclude 'workspace/KB/сток/'
  # .env едет отдельной строкой ниже, не под --delete.
  --exclude '.env'
  --exclude '.git/'
  # Метку деплоя пишем ниже, --delete её сносить не должен.
  --exclude '.deployed'
)

ssh_do() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" "$@"; }

echo "→ на проде сейчас: $(ssh_do "cat $REMOTE/.deployed 2>/dev/null || echo 'метки нет'")"

if [[ "${DIVO_SKIP_GUARD:-0}" != "1" ]]; then
  echo "→ предполётная проверка"

  # Выкатываем только то, что в git: иначе история правок теряется, а
  # расхождение мака и прода не видно до следующего разбора.
  dirty="$(git -c core.quotepath=false -C "$LOCAL" status --porcelain -- "$LOCAL" || true)"
  if [[ -n "$dirty" ]]; then
    echo "СТОП: в проекте есть незакоммиченные изменения." >&2
    echo "$dirty" | sed 's/^/    /' >&2
    echo "Закоммить их и повтори выкат." >&2
    exit 1
  fi

  # Что именно затрётся на проде. Уже случалось, что прод был новее мака и
  # rsync --delete откатывал живые правки.
  # Прод .env перезаписывается маковским. Ключ, которого нет локально, пропадёт.
  lost="$(comm -23 \
    <(ssh_do "grep -oE '^[A-Z_]+=' $REMOTE/.env 2>/dev/null | sort" || true) \
    <(grep -oE '^[A-Z_]+=' "$LOCAL/.env" 2>/dev/null | sort || true) \
    | grep -v '^GOOGLE_SA_PATH=$' || true)"
  if [[ -n "$lost" ]]; then
    echo "СТОП: в проде есть ключи, которых нет в локальном .env:" >&2
    echo "$lost" | sed 's/^/    /' >&2
    echo "Перенеси их в локальный .env, иначе выкат их потеряет." >&2
    exit 1
  fi

  echo "→ что изменится на проде:"
  plan="$(rsync -az --delete --dry-run --itemize-changes -e "ssh -i $KEY" \
    "${EXCLUDES[@]}" "$LOCAL/" "$HOST:$REMOTE/" | grep -v '^\.d' || true)"
  if [[ -z "$plan" ]]; then
    echo "    прод уже совпадает с этим коммитом"
  else
    echo "$plan" | sed 's/^/    /'
    if [[ "${DIVO_YES:-0}" != "1" ]]; then
      if [[ -t 0 ]]; then
        read -r -p "Применяем? [y/N] " ans
        [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "отменено"; exit 1; }
      else
        echo "СТОП: подтверди список выше - запусти с DIVO_YES=1." >&2
        exit 1
      fi
    fi
  fi
fi

echo "→ гашу бота (если запущен)"
ssh_do "systemctl stop divo-ai-bot 2>/dev/null || true"

echo "→ синхронизирую код в $REMOTE"
rsync -az --delete -e "ssh -i $KEY" "${EXCLUDES[@]}" "$LOCAL/" "$HOST:$REMOTE/"

sha="$(git -C "$LOCAL" rev-parse --short HEAD 2>/dev/null || echo 'без git')"
ssh_do "printf '%s\n' '$sha $(date +%F_%H:%M) с $(hostname -s)' > $REMOTE/.deployed"

echo "→ .env (секреты отдельно, не под --delete)"
rsync -az -e "ssh -i $KEY" "$LOCAL/.env" "$HOST:$REMOTE/.env"
# Сервисный ключ Google живёт в divo-cme-stock, туда и смотрим.
ssh_do "grep -q '^GOOGLE_SA_PATH=/root' $REMOTE/.env || \
  sed -i 's|^GOOGLE_SA_PATH=.*|GOOGLE_SA_PATH=/root/divo-cme-stock/google_sa.json|' $REMOTE/.env; \
  grep -q '^GOOGLE_SA_PATH=' $REMOTE/.env || \
  echo 'GOOGLE_SA_PATH=/root/divo-cme-stock/google_sa.json' >> $REMOTE/.env; \
  chmod 600 $REMOTE/.env"

echo "→ venv и зависимости"
ssh_do "cd $REMOTE && (test -d .venv || python3 -m venv .venv) && \
  .venv/bin/pip -q install -U pip && .venv/bin/pip -q install -r requirements.txt"

echo "→ systemd"
ssh_do "install -m 644 $REMOTE/deploy/divo-proxy.service /etc/systemd/system/divo-proxy.service && \
  install -m 644 $REMOTE/deploy/divo-ai-bot.service /etc/systemd/system/divo-ai-bot.service && \
  systemctl daemon-reload && systemctl enable --now divo-proxy && systemctl enable --now divo-ai-bot"

echo "→ статус"
sleep 4
ssh_do "systemctl is-active divo-ai-bot; journalctl -u divo-ai-bot -n 20 --no-pager"
