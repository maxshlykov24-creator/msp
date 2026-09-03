#!/bin/bash
# Линия 1 (+18187911783): кто первым принимает звонок — Павел в свою смену, иначе
# Александра. Скрипт приводит `.env` к состоянию, которое положено по часам, и
# ничего не делает, если оно уже верное.
#
# Почему так, а не одноразовыми таймерами на границы смены (как было 24.08):
# 25.08 в 04:00 таймер вернул линию Александре, новых никто не поставил — и до
# 31.08 звонки в смену Павла уходили на Александру, один клиент так и потерялся.
# Плюс деплой хаба перезаписывает `.env` целиком и затирает состояние смены.
# Периодический idempotent-прогон закрывает оба случая и переход на зимнее время.
#
# Ставится в /usr/local/bin/lb-line1-auto.sh, зовётся таймером lb-line1-auto.timer
# каждые 10 минут. Журнал общий с lb-line1-owner.sh.
set -euo pipefail

APP=/opt/licensebridge-tilda-webhook
OWNER_SH=/usr/local/bin/lb-line1-owner.sh
LOG=/var/log/lb-line1-owner.log
SHIFT_TZ=America/Los_Angeles
SHIFT_FROM=16      # смена Павла начинается в 16:00 по Калифорнии
SHIFT_TO=21        # и заканчивается в 21:00

hour=$(TZ="$SHIFT_TZ" date +%-H)
if [ "$hour" -ge "$SHIFT_FROM" ] && [ "$hour" -lt "$SHIFT_TO" ]; then
    want=pavel; want_did="1:101,3:103"
else
    want=sasha;  want_did="1:103,3:103"
fi

have_did=$(grep -a "^TELEPHONY_DID_EXT=" "$APP/.env" | cut -d= -f2- || true)
if [ "$have_did" = "$want_did" ]; then
    exit 0
fi

printf "%s авто-смена: %s -> %s (%s %s:00)\n" "$(date "+%F %T %Z")" \
    "${have_did:-пусто}" "$want_did" "$SHIFT_TZ" "$hour" >> "$LOG"
"$OWNER_SH" "$want"
