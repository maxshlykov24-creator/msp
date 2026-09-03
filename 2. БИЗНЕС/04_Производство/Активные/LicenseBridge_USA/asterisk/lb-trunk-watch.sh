#!/bin/bash
# Наблюдатель за транками TG400: пишет строку в журнал только при смене состояния
# или при отказе дозвона. Ничего не перезапускает и не лечит — только факты,
# чтобы разбор отвала не начинался с гипотез.
#
# С 19.08.2026 не только пишет, но и зовёт: отвал добавочного дольше
# LB_WATCH_EXT_ALERT_MIN и падение транка/регистрации уходят в Telegram через
# хаб (POST /internal/alert). Причина — 07.08 отвал софтфона 102 лёг в этот
# журнал, а узнали мы о нём от клиента 13.08: неделю клиенты звонили в пустоту.
# Токен бота на Asterisk не держим, он один — в .env хаба.
#
# Установка: cron раз в минуту, см. asterisk/README.md.
set -uo pipefail

LOG=${LB_WATCH_LOG:-/var/log/lb-trunk-watch.log}
STATE=${LB_WATCH_STATE:-/var/lib/lb-trunk-watch.state}
FULL=${LB_WATCH_FULL:-/var/log/asterisk/full}
TRUNKS=${LB_WATCH_TRUNKS:-"telnyx tg400trunk tg400trunk2 tg400trunk3"}
REGS=${LB_WATCH_REGS:-"telnyx_reg"}
EXTS=${LB_WATCH_EXTS:-"101 102 103"}
# Состояние пишем по всем добавочным, а звоним только по тем, кто должен быть на
# линии весь день. 101 (Павел, владелец, с 24.08.2026) сидит короткими сменами и
# офлайн по своему графику — это норма, а не отвал. Вечный алерт про него научил
# бы игнорировать все алерты подряд.
ALERT_EXTS=${LB_WATCH_ALERT_EXTS:-"102 103"}
MAX_LOG_BYTES=${LB_WATCH_MAX_LOG:-5242880}
# сколько минут добавочный должен лежать, чтобы это был отвал, а не перезапуск
# MicroSIP или обед: короче — алерт превратится в фон, который перестанут читать
EXT_ALERT_MIN=${LB_WATCH_EXT_ALERT_MIN:-15}
ENV_FILE=${LB_WATCH_ENV:-/etc/asterisk/lb-hub.env}

mkdir -p "$(dirname "$STATE")"
touch "$STATE"

now() { date '+%F %T'; }

say() { echo "$(now) $*" >>"$LOG"; }

# Алерт в Telegram через хаб. Без lb-hub.env молча продолжаем: наблюдатель
# обязан писать журнал даже когда связи с хабом нет.
alert() {
    text="$1"; dedup="${2:-}"
    [[ -r "$ENV_FILE" ]] || return 0
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    [[ -n "${LB_HUB:-}" && -n "${LB_KEY:-}" ]] || return 0
    curl -s -m 10 -o /dev/null --data-urlencode "key=$LB_KEY" \
        --data-urlencode "text=$text" --data-urlencode "dedup=$dedup" \
        "$LB_HUB/internal/alert" || say "АЛЕРТ НЕ УШЁЛ: $text"
}

# состояние «лежит с такого-то момента» держим в том же файле state: без метки
# времени нельзя отличить отвал на 15 минут от секундной перерегистрации
down_since() {
    key="down_$1"
    line=$(grep "^$key=" "$STATE" 2>/dev/null | tail -1 | cut -d= -f2-)
    echo "${line:-}"
}

mark_down() {
    key="down_$1"; value="$2"
    grep -v "^$key=" "$STATE" >"$STATE.tmp" 2>/dev/null || true
    mv "$STATE.tmp" "$STATE" 2>/dev/null || true
    [[ -n "$value" ]] && echo "$key=$value" >>"$STATE"
}

# Держит метку «лежит с» и зовёт один раз, когда простой перевалил порог.
# Восстановление тоже сообщаем: молчание после алерта нельзя читать как «ок».
watch_down() {
    key="$1"; is_down="$2"; threshold_min="$3"; label="$4"
    since=$(down_since "$key")
    stamp=$(date +%s)
    if [[ "$is_down" == "1" ]]; then
        if [[ -z "$since" ]]; then
            mark_down "$key" "$stamp"
            return 0
        fi
        minutes=$(( (stamp - ${since%%:*}) / 60 ))
        if [[ "$since" != *":sent" && "$minutes" -ge "$threshold_min" ]]; then
            text="🔴 <b>Отключение линии</b> | LicenseBridge

$label не в сети $minutes мин.

<i>Менеджер не сможет принимать и совершать звонки.</i>"
            alert "$text" "$key"
            say "АЛЕРТ: $label down ${minutes}m"
            mark_down "$key" "${since%%:*}:sent"
        fi
    elif [[ -n "$since" ]]; then
        [[ "$since" == *":sent" ]] && {
            text="🟢 <b>Линия восстановлена</b> | LicenseBridge

$label снова на связи."
            alert "$text" "$key-up"
            say "ВОССТАНОВЛЕНО: $label"
        }
        mark_down "$key" ""
    fi
}

# состояние транков и добавочных: только статус, без RTT и хешей — иначе журнал
# будет меняться каждую минуту и потеряет смысл
current=""
endpoints=$(asterisk -rx "pjsip show endpoints" 2>/dev/null)
for trunk in $TRUNKS; do
    status=$(asterisk -rx "pjsip show aor $trunk" 2>/dev/null \
        | awk '/Contact:/ && !/ContactUri/ {print $4}' | head -1)
    current+="$trunk=${status:-none};"
    # транк держим строже добавочного: пока он лежит, не идут ни исходящие, ни входящие
    [[ "$status" == "Avail" ]] && down=0 || down=1
    watch_down "trunk-$trunk" "$down" "${LB_WATCH_TRUNK_ALERT_MIN:-5}" "транк $trunk"
done
# регистрация на облачном провайдере: слетела — исходящие и входящие встанут,
# при этом endpoint может ещё показывать Avail
registrations=$(asterisk -rx "pjsip show registrations" 2>/dev/null)
for reg in $REGS; do
    status=$(echo "$registrations" | awk -v r="$reg/" 'index($1, r)==1 {print $3}' | head -1)
    current+="$reg=${status:-none};"
    [[ "$status" == "Registered" ]] && down=0 || down=1
    watch_down "reg-$reg" "$down" "${LB_WATCH_TRUNK_ALERT_MIN:-5}" "регистрация $reg"
done
for ext in $EXTS; do
    state=$(echo "$endpoints" | awk -v e="$ext" '$1=="Endpoint:" && $2==e {
        $1=""; $2=""; sub(/ *[0-9]+ of .*/, ""); gsub(/^ +| +$/, ""); print }')
    current+="$ext=${state:-unknown};"
    # «Unavailable» и пустой ответ — софтфон не зарегистрирован; «Not in use» и
    # «In use» одинаково живые, менеджер просто не на линии прямо сейчас
    case "${state:-unknown}" in
        *Unavailable*|unknown) down=1 ;;
        *) down=0 ;;
    esac
    case " $ALERT_EXTS " in
        *" $ext "*) watch_down "ext-$ext" "$down" "$EXT_ALERT_MIN" "добавочный $ext" ;;
    esac
done

previous=$(grep '^state=' "$STATE" 2>/dev/null | tail -1 | cut -d= -f2-)
if [[ "$current" != "$previous" ]]; then
    say "СМЕНА СОСТОЯНИЯ: $current"
    grep -v '^state=' "$STATE" >"$STATE.tmp" 2>/dev/null || true
    mv "$STATE.tmp" "$STATE" 2>/dev/null || true
    echo "state=$current" >>"$STATE"
fi

# отказы дозвона с прошлого прохода: читаем только новый хвост лога
offset=$(grep '^offset=' "$STATE" 2>/dev/null | tail -1 | cut -d= -f2)
size=$(stat -c %s "$FULL" 2>/dev/null || echo 0)
[[ -z "${offset:-}" || "$offset" -gt "$size" ]] && offset=$size

if [[ "$size" -gt "$offset" ]]; then
    tail -c +$((offset + 1)) "$FULL" 2>/dev/null \
        | grep -aoE "Everyone is busy/congested[^\"]*|Got SIP response [0-9]{3}[^\"]*|chan_pjsip.*(480|503)" \
        | sort | uniq -c \
        | while read -r count text; do
              say "ОТКАЗ ДОЗВОНА x$count: $text"
          done
fi
grep -v '^offset=' "$STATE" >"$STATE.tmp" 2>/dev/null || true
mv "$STATE.tmp" "$STATE" 2>/dev/null || true
echo "offset=$size" >>"$STATE"

# журнал только на смены состояния, но подстрахуемся от разрастания
if [[ -f "$LOG" ]] && [[ $(stat -c %s "$LOG") -gt "$MAX_LOG_BYTES" ]]; then
    tail -c $((MAX_LOG_BYTES / 2)) "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
