#!/usr/bin/env bash
# Выкладка на VPS DIVO. Запускать локально из корня сервиса:
#   bash deploy/up.sh
#
# Выкатывается только закоммиченное. Обход: DIVO_SKIP_GUARD=1 bash deploy/up.sh
set -euo pipefail

HOST="${DIVO_HOST:-root@104.171.136.226}"
KEY="${DIVO_SSH_KEY:-$HOME/.ssh/divo_deploy}"
REMOTE=/root/divo-cme-stock
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$HOST")

PAYLOAD=(
  "$DIR/app" "$DIR/Dockerfile" "$DIR/docker-compose.yml"
  "$DIR/requirements.txt" "$DIR/.env.example" "$DIR/deploy"
)
EXCLUDES=(
  --exclude '.env' --exclude '__pycache__' --exclude '.venv'
  --exclude 'data' --exclude '.pytest_cache' --exclude 'google_sa.json'
)

echo "── на проде сейчас: $("${SSH[@]}" "cat $REMOTE/.deployed 2>/dev/null || echo 'метки нет'")"

if [[ "${DIVO_SKIP_GUARD:-0}" != "1" ]]; then
  # Тесты дешёвые, а колонки A-O читает amo: ломать их выкатом нельзя.
  if command -v python3 >/dev/null && [[ -d "$DIR/tests" ]]; then
    echo "── тесты"
    (cd "$DIR" && python3 -m pytest -q) || { echo "СТОП: тесты не прошли" >&2; exit 1; }
  fi

  # Выкатываем только то, что в git: иначе прод и мак расходятся молча.
  dirty="$(git -c core.quotepath=false -C "$DIR" status --porcelain -- "$DIR" || true)"
  if [[ -n "$dirty" ]]; then
    echo "СТОП: в проекте есть незакоммиченные изменения." >&2
    echo "$dirty" | sed 's/^/    /' >&2
    echo "Закоммить их и повтори выкат." >&2
    exit 1
  fi

  echo "── что изменится на проде:"
  plan="$(rsync -az --delete --dry-run --itemize-changes \
    -e "ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" \
    "${EXCLUDES[@]}" "${PAYLOAD[@]}" "$HOST:$REMOTE/" | grep -v '^\.d' || true)"
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

echo "── rsync → $HOST:$REMOTE"
"${SSH[@]}" "mkdir -p $REMOTE"
rsync -az --delete \
  -e "ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" \
  "${EXCLUDES[@]}" "${PAYLOAD[@]}" "$HOST:$REMOTE/"

sha="$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo 'без git')"
"${SSH[@]}" "printf '%s\n' '$sha $(date +%F_%H:%M) с $(hostname -s)' > $REMOTE/.deployed"

if [[ -f "$DIR/google_sa.json" ]]; then
  echo "── google_sa.json"
  scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
    "$DIR/google_sa.json" "$HOST:$REMOTE/google_sa.json"
fi

"${SSH[@]}" "test -f $REMOTE/.env" || {
  echo "создаю $REMOTE/.env из примера (дописать CME_CLIENT_ID/SECRET и Telegram)"
  "${SSH[@]}" "cp $REMOTE/.env.example $REMOTE/.env"
}

echo "── docker compose up"
"${SSH[@]}" "cd $REMOTE && docker compose up -d --build"
"${SSH[@]}" "cd $REMOTE && docker compose ps"
echo "логи: ssh -i $KEY $HOST 'cd $REMOTE && docker compose logs --tail=80 worker'"
