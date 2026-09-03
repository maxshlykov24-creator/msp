#!/bin/bash
# Возврат линии 3 людям: DID +18188066735 снова идёт в меню и на Александру,
# недозвон и нерабочее время — как до появления агента.
#
# Ставится по расписанию на 18:00 МСК (15:00 UTC), пока агент Pleep рвёт
# разговор на 1.5-й секунде (см. README, раздел про 13.08):
#   systemd-run --on-calendar='2026-08-13 15:00:00' --unit=lb-pleep-off \
#       /usr/local/bin/lb-pleep-off.sh
#
# Обратно включает человек и только осознанно:
#   asterisk -rx 'dialplan set global LB_PLEEP_DID3 1'
#   asterisk -rx 'dialplan set global LB_AI_ON 1'
#
# Значения по умолчанию в extensions.conf тоже безопасные, поэтому перезапуск
# или `dialplan reload` сам возвращает звонки людям.
set -euo pipefail

LOG=/var/log/lb-pleep-switch.log

for pair in "LB_PLEEP_DID3 0" "LB_AI_ON 0"; do
    asterisk -rx "dialplan set global $pair" >/dev/null
done

state=$(asterisk -rx 'dialplan show globals' | grep -aE 'LB_PLEEP_DID3|LB_AI_ON' | tr -d ' ' | paste -sd' ')
printf '%s агент выключен, линия 3 людям: %s\n' "$(date '+%F %T %Z')" "$state" >> "$LOG"
