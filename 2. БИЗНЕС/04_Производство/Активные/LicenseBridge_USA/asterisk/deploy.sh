#!/bin/bash
# Выкладка диалплана и скрипта отчёта на Asterisk LicenseBridge.
#
#   ./deploy.sh            — залить и перечитать диалплан
#   ./deploy.sh --dry-run  — показать разницу с сервером, ничего не менять
#
# Секреты живут только на сервере, deploy их не трогает:
#   /etc/asterisk/lb-hub.env, /etc/asterisk/extensions_lb_local.conf — LB_HUB/LB_KEY
#   /etc/asterisk/pjsip_telnyx_auth.conf — логин/пароль SIP-транка Telnyx
set -euo pipefail

HOST=${LB_AST_HOST:-159.65.97.156}
KEY=${LB_AST_KEY:-$HOME/.ssh/licensebridge_do}
DIR="$(cd "$(dirname "$0")" && pwd)"
# -n: без него ssh читает stdin выкладки и в неинтерактивном запуске (агент, CI)
# команда висит бесконечно; для блока с heredoc ниже нужен отдельный вызов без -n
SSH=(ssh -n -i "$KEY" -o StrictHostKeyChecking=no "root@$HOST")
SSH_STDIN=(ssh -i "$KEY" -o StrictHostKeyChecking=no "root@$HOST")

if [[ "${1:-}" == "--dry-run" ]]; then
    echo "── diff extensions.conf ──"
    "${SSH[@]}" "cat /etc/asterisk/extensions.conf" | diff -u - "$DIR/extensions.conf" || true
    echo "── diff lb-call-report.sh ──"
    "${SSH[@]}" "cat /usr/local/bin/lb-call-report.sh 2>/dev/null" | diff -u - "$DIR/lb-call-report.sh" || true
    echo "── diff pjsip_telnyx.conf ──"
    "${SSH[@]}" "cat /etc/asterisk/pjsip_telnyx.conf 2>/dev/null" | diff -u - "$DIR/pjsip_telnyx.conf" || true
    echo "── diff manager.conf ──"
    "${SSH[@]}" "cat /etc/asterisk/manager.conf 2>/dev/null" | diff -u - "$DIR/manager.conf" || true
    exit 0
fi

STAMP=$(date +%Y%m%d-%H%M%S)
"${SSH[@]}" "cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak-$STAMP"

scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/extensions.conf" "root@$HOST:/etc/asterisk/extensions.conf"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/lb-call-report.sh" "root@$HOST:/usr/local/bin/lb-call-report.sh"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/pjsip_telnyx.conf" "root@$HOST:/etc/asterisk/pjsip_telnyx.conf"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/lb-trunk-watch.sh" "root@$HOST:/usr/local/bin/lb-trunk-watch.sh"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/manager.conf" "root@$HOST:/etc/asterisk/manager.conf"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/pjsip_pleep.conf" "root@$HOST:/etc/asterisk/pjsip_pleep.conf"
scp -i "$KEY" -o StrictHostKeyChecking=no \
    "$DIR/lb-pleep-off.sh" "root@$HOST:/usr/local/bin/lb-pleep-off.sh"

"${SSH_STDIN[@]}" bash -s <<'REMOTE'
set -e
chown asterisk:asterisk /etc/asterisk/extensions.conf /etc/asterisk/pjsip_telnyx.conf \
    /etc/asterisk/pjsip_pleep.conf
chmod 640 /etc/asterisk/extensions.conf
chmod 644 /etc/asterisk/pjsip_telnyx.conf /etc/asterisk/pjsip_pleep.conf
test -f /etc/asterisk/pjsip_pleep_auth.conf || {
    echo "НЕТ /etc/asterisk/pjsip_pleep_auth.conf — агент Pleep не зарегистрируется"; exit 1; }
# скрипт и секреты читает демон asterisk, а не root
chown root:asterisk /usr/local/bin/lb-call-report.sh /etc/asterisk/lb-hub.env
chmod 750 /usr/local/bin/lb-call-report.sh /usr/local/bin/lb-trunk-watch.sh \
    /usr/local/bin/lb-pleep-off.sh
chmod 640 /etc/asterisk/lb-hub.env
test -f /etc/asterisk/pjsip_telnyx_auth.conf || {
    echo "НЕТ /etc/asterisk/pjsip_telnyx_auth.conf — транк Telnyx не зарегистрируется"; exit 1; }
test -f /etc/asterisk/extensions_lb_local.conf || {
    echo "НЕТ /etc/asterisk/extensions_lb_local.conf — диалплан не узнает адрес хаба"; exit 1; }
test -f /etc/asterisk/lb-hub.env || {
    echo "НЕТ /etc/asterisk/lb-hub.env — скрипт отчёта не сможет позвать хаб"; exit 1; }
# AMI: пароль и permit хаба лежат отдельно от репозитория (см. manager.conf)
if [ -f /etc/asterisk/manager_lb_local.conf ]; then
    chown root:asterisk /etc/asterisk/manager.conf /etc/asterisk/manager_lb_local.conf
    chmod 640 /etc/asterisk/manager.conf /etc/asterisk/manager_lb_local.conf
    asterisk -rx 'module reload manager' >/dev/null
else
    echo "ВНИМАНИЕ: нет /etc/asterisk/manager_lb_local.conf — AI-звонки хаб инициировать не сможет"
fi
asterisk -rx 'dialplan reload'
asterisk -rx 'module reload res_pjsip.so' >/dev/null
asterisk -rx 'dialplan show lb-inbound' | tail -5
asterisk -rx 'dialplan show lb-ai-pleep' | tail -3
asterisk -rx 'pjsip show registrations'
REMOTE

echo "готово: диалплан перечитан на $HOST (бэкап extensions.conf.bak-$STAMP)"
