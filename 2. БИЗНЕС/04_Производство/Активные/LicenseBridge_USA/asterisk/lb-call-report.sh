#!/bin/sh
# Итог звонка → хаб LicenseBridge. Запускается hangup-handler'ом Asterisk в фоне.
#
# Аргументы: uniqueid phone direction answeredtime dialstatus rec did tried
#            branch minsec ext
# ext — добавочный менеджера (кто позвонил или кто снял трубку). По нему хаб
# ставит автора примечания-звонка в Kommo; пусто — хаб решает сам, как раньше.
# Секреты (LB_HUB, LB_KEY) — в /etc/asterisk/lb-hub.env, файл только для root.
set -u

UNIQ="${1:-}"; PHONE="${2:-}"; DIRECTION="${3:-in}"; ANSWERED="${4:-0}"
STATUS="${5:-}"; REC="${6:-}"; DID="${7:-}"; TRIED="${8:-0}"
BRANCH="${9:-}"; MINSEC="${10:-12}"; EXT="${11:-}"

REC_DIR=/var/spool/asterisk/monitor
. /etc/asterisk/lb-hub.env

# MixMonitor дописывает файл уже после hangup-handler'а — даём ему закрыться
sleep 3

case "$ANSWERED" in ''|*[!0-9]*) ANSWERED=0 ;; esac
case "$MINSEC" in ''|*[!0-9]*) MINSEC=12 ;; esac

# Голосовая почта RingCentral снимает трубку сама, и Asterisk считает это
# ответом. Короткий «разговор» в ветке обслуживания — значит клиент попал на
# автоответчик: помечаем звонок пропущенным, чтобы хаб поставил задачу Полине.
# Запись оставляем: по ней слышно, ответил человек или автоответчик.
TALK=$ANSWERED
if [ "$BRANCH" = "service" ] && [ "$ANSWERED" -gt 0 ] && [ "$ANSWERED" -lt "$MINSEC" ]; then
    logger -t lb-call "voicemail uid=$UNIQ sec=$ANSWERED < $MINSEC"
    ANSWERED=0
    TRIED=1
    STATUS=VOICEMAIL
fi

LINK=""
if [ -n "$REC" ] && [ -f "$REC_DIR/$REC.wav" ]; then
    if [ "$TALK" -gt 0 ]; then
        # mp3 вместо wav: плеер Kommo играет сразу, файл в разы меньше.
        # 16 кГц / 64 kbps, а не 48 kbps на исходных 8 кГц: в режиме MPEG-2.5 lame
        # даёт «бульканье» — на слух это и была «плохая запись». loudnorm выравнивает
        # громкость (разговоры приходили тихими, ~-22 dBFS), true peak держит лимитер,
        # highpass убирает гул линии.
        if ffmpeg -nostdin -y -loglevel error -i "$REC_DIR/$REC.wav" \
                  -af "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11" -ar 16000 \
                  -codec:a libmp3lame -b:a 64k -ac 1 "$REC_DIR/$REC.mp3" 2>/dev/null; then
            rm -f "$REC_DIR/$REC.wav"
            LINK="$REC.mp3"
        else
            LINK="$REC.wav"
        fi
    else
        # разговора не было: на диске остался бы только проигрыш меню
        rm -f "$REC_DIR/$REC.wav"
    fi
fi

if [ "$ANSWERED" -gt 0 ]; then
    ENDPOINT=finished
elif [ "$DIRECTION" = "in" ]; then
    # Любой входящий без разговора — пропущенный, даже если клиент бросил трубку
    # на меню и до менеджеров не дошло. Раньше такие уходили как finished: строка
    # в карточке есть, задачи «перезвонить» нет, и звонок терялся в ленте.
    ENDPOINT=missed
else
    ENDPOINT=finished        # исходящий без ответа — задача не нужна
fi

if ! curl -sS -m 20 --retry 3 --retry-delay 3 -o /dev/null \
     -X POST "$LB_HUB/internal/call/$ENDPOINT" \
     -H "X-Internal-Key: $LB_KEY" \
     --data-urlencode "uniqueid=$UNIQ" \
     --data-urlencode "phone=$PHONE" \
     --data-urlencode "direction=$DIRECTION" \
     --data-urlencode "duration=$ANSWERED" \
     --data-urlencode "disposition=$STATUS" \
     --data-urlencode "recording=$LINK" \
     --data-urlencode "did=$DID" \
     --data-urlencode "ext=$EXT" \
     --data-urlencode "branch=$BRANCH"; then
    logger -t lb-call "FAILED $ENDPOINT uid=$UNIQ phone=$PHONE"
    exit 1
fi

logger -t lb-call "$ENDPOINT uid=$UNIQ phone=$PHONE dur=$ANSWERED status=$STATUS rec=$LINK branch=$BRANCH ext=$EXT"
