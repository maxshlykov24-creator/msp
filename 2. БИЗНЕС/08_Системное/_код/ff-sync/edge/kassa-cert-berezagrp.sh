#!/bin/sh
# Выпуск Let's Encrypt для berezagrp.ru на внешнем краю (VPS 72.56.240.103).
# Запускать ТОЛЬКО после того, как A-записи домена смотрят на 72.56.240.103:
# HTTP-01 проверяет тот адрес, который стоит в DNS.
#
# До 15.09.2026 сертификат приходилось выпускать вручную через DNS-01 и копировать
# с прода: домен смотрел на ноду 72.56.8.43, до которой валидаторы не доходили.
# С краем на живом VPS проверка проходит сама, дальше продлевает certbot.timer.
#
# Копия на сервере: /root/cert-berezagrp.sh
set -e

DOMAIN=berezagrp.ru
WEBROOT=/var/www/certbot
CERTDIR=/etc/nginx/certs/berezagrp

mkdir -p "$WEBROOT"

# сверяем DNS до запроса: иначе сожжём попытку в лимите Let's Encrypt
for host in "$DOMAIN" "www.$DOMAIN"; do
    ip=$(dig +short A "$host" | tail -1)
    if [ "$ip" != "72.56.240.103" ]; then
        echo "DNS ещё не переключён: $host -> ${ip:-пусто}, ждём 72.56.240.103" >&2
        exit 1
    fi
done

certbot certonly --webroot -w "$WEBROOT" \
    -d "$DOMAIN" -d "www.$DOMAIN" \
    --cert-name "$DOMAIN" \
    --agree-tos --email info@msproduct.ru --no-eff-email \
    --non-interactive --keep-until-expiring

# nginx читает сертификат из своей папки, поэтому кладём симлинки на live:
# при продлении файлы обновятся на месте, руками копировать больше не нужно
mkdir -p "$CERTDIR"
ln -sf "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$CERTDIR/fullchain.pem"
ln -sf "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$CERTDIR/privkey.pem"

nginx -t
systemctl reload nginx

certbot certificates 2>/dev/null | grep -A3 "Certificate Name: $DOMAIN"
echo "OK: сертификат на краю, продление через certbot.timer"
