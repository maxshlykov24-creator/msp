#!/bin/bash
# Переезд ff-sync на новый VPS. Запускать С МАКА, сервер указывать первым аргументом:
#   bash edge/переезд-на-новый-vps.sh 194.87.27.142
#
# Зачем переезд: нода 72.56.8.43 снаружи не маршрутизируется. Замер 15.09.2026,
# check-host: до неё таймаутят 8 узлов из 12 и на 80, и на 22 — то есть это не
# файрвол на порту, а сеть хостера. Новый сервер по тому же замеру виден с 11
# узлов из 12, порты 80 и 443 свободны.
#
# Логика безопасности: сначала переносим и поднимаем ТОЛЬКО web на новом сервере,
# воркер не включаем. Два воркера одновременно трогать площадки не должны —
# на этом уже спотыкались 10.09. Воркер стартует последним шагом, после того как
# на старом сервере он погашен и база перелита ещё раз.
set -euo pipefail

NEW="${1:?укажи IP нового сервера}"
OLD_JUMP=root@72.56.240.103          # живой VPS кассы, через него ходим на старый прод
OLD=root@72.56.8.43
SSH_OLD=(ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 -J "$OLD_JUMP" "$OLD")
SSH_NEW=(ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 "root@$NEW")

say() { printf '\n=== %s ===\n' "$1"; }

say "1. Проверяю новый сервер"
"${SSH_NEW[@]}" 'hostname; . /etc/os-release && echo "$PRETTY_NAME"; free -m | awk "/Mem:/{print \"RAM MB:\", \$2}"; df -h / | tail -1'

say "2. Ставлю docker, если его нет"
"${SSH_NEW[@]}" 'command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sh)
docker --version; docker compose version | head -1'

say "3. Забираю код и .env со старого сервера"
tmp=$(mktemp -d)
"${SSH_OLD[@]}" 'tar czf - -C /opt ff-sync' > "$tmp/ff-sync.tgz"
ls -lh "$tmp/ff-sync.tgz"

say "4. Кладу код на новый сервер"
scp -o ConnectTimeout=20 "$tmp/ff-sync.tgz" "root@$NEW:/tmp/"
"${SSH_NEW[@]}" 'mkdir -p /opt && tar xzf /tmp/ff-sync.tgz -C /opt && rm -f /tmp/ff-sync.tgz && ls /opt/ff-sync | head'

say "5. Собираю образ на новом сервере"
# собираем здесь, а не переносим: на старой ноде сборка падала, deb.debian.org
# с неё недоступен, и образ там латали через Dockerfile.patch
"${SSH_NEW[@]}" 'cd /opt/ff-sync && docker compose build'

say "6. Первая копия базы (сервис на старом ещё работает)"
"${SSH_OLD[@]}" 'docker run --rm -v ff-sync_ff_data:/data alpine tar czf - -C /data ff.db' > "$tmp/ff.db.tgz"
ls -lh "$tmp/ff.db.tgz"
scp -o ConnectTimeout=20 "$tmp/ff.db.tgz" "root@$NEW:/tmp/"
"${SSH_NEW[@]}" 'docker volume create ff-sync_ff_data >/dev/null
docker run --rm -v ff-sync_ff_data:/data -v /tmp/ff.db.tgz:/in.tgz alpine tar xzf /in.tgz -C /data
docker run --rm -v ff-sync_ff_data:/data alpine ls -lh /data'

say "7. Поднимаю только web, воркер выключен"
"${SSH_NEW[@]}" 'cd /opt/ff-sync && docker compose up -d web edge && docker compose ps --format "{{.Service}} {{.Status}}"
sleep 3; curl -sS -m 5 http://127.0.0.1:8787/health; echo'

say "8. Сертификат Let'\''s Encrypt на имя по IP"
# sslip.io отдаёт A-запись по имени, поэтому HTTP-01 проходит без правки DNS домена
"${SSH_NEW[@]}" "command -v certbot >/dev/null || (apt-get update -qq && apt-get install -y -qq certbot)
mkdir -p /opt/ff-sync/edge/acme
certbot certonly --webroot -w /opt/ff-sync/edge/acme \
  -d ${NEW}.sslip.io --cert-name ff-sync \
  --agree-tos --email info@msproduct.ru --no-eff-email --non-interactive --keep-until-expiring
cat /etc/letsencrypt/live/ff-sync/fullchain.pem > /opt/ff-sync/edge/certs/cert.pem
cp /etc/letsencrypt/live/ff-sync/privkey.pem /opt/ff-sync/edge/certs/key.pem
chmod 644 /opt/ff-sync/edge/certs/cert.pem; chmod 600 /opt/ff-sync/edge/certs/key.pem
cd /opt/ff-sync && docker compose exec -T edge nginx -s reload"

say "9. Проверка снаружи"
curl -sS -m 15 -o /dev/null -w "https://${NEW}.sslip.io/ -> %{http_code}, сертификат verify=%{ssl_verify_result}\n" "https://${NEW}.sslip.io/"
curl -sS -m 15 "https://${NEW}.sslip.io/health"; echo

cat <<TXT

Дальше — окно переключения, руками и по одному шагу:

  1. На старом гасим воркер и web:
     ssh -J $OLD_JUMP $OLD 'cd /opt/ff-sync && docker compose stop worker web'
  2. Переливаем базу ещё раз (за время сборки в неё капали заказы) — повтори шаг 6.
  3. На новом поднимаем всё, включая воркер:
     ssh root@$NEW 'cd /opt/ff-sync && docker compose up -d'
  4. Смотрим, что воркер тянет площадки и МойСклад:
     ssh root@$NEW 'docker logs --tail 40 ff-sync-worker-1'
  5. Складу даём https://${NEW}.sslip.io/ , домен переводим отдельно.

Старый сервер не гасим и не удаляем: там остаётся база на момент переезда.
TXT
rm -rf "$tmp"
