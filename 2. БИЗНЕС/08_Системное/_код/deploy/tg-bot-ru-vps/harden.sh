#!/usr/bin/env bash
# Базовая защита RU VPS (шаг "sec"): SSH-ключ, отключение парольного входа, ufw.
# Запускать НА СЕРВЕРЕ от root:  bash harden.sh "ssh-ed25519 AAAA... you@host"
#
# ВАЖНО (чтобы не отрезать себе доступ):
#   1) НЕ закрывай текущую SSH-сессию до конца проверки.
#   2) Сначала открой ВТОРОЙ терминал и убедись, что вход по ключу работает,
#      и только потом соглашайся отключить пароль.
set -euo pipefail

PUBKEY="${1:-}"
if [[ -z "$PUBKEY" ]]; then
  echo "Использование: bash harden.sh \"<твой публичный SSH-ключ>\"" >&2
  echo "Сгенерировать локально: ssh-keygen -t ed25519 -C \"tgbot-ru-vps\"" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запусти от root." >&2
  exit 1
fi

echo "==> 1/4 Добавляю SSH-ключ в /root/.ssh/authorized_keys"
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if ! grep -qF "$PUBKEY" /root/.ssh/authorized_keys; then
  echo "$PUBKEY" >> /root/.ssh/authorized_keys
  echo "    ключ добавлен"
else
  echo "    ключ уже присутствует"
fi

echo "==> 2/4 Настраиваю ufw (открыты только 22, 80, 443)"
if ! command -v ufw >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y ufw
fi
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
yes | ufw enable
ufw status verbose

echo "==> 3/4 Смена root-пароля"
echo "    Задай новый надёжный root-пароль (или Ctrl-C, если сменишь позже):"
passwd root || echo "    пропущено — не забудь сменить пароль вручную (passwd root)"

echo "==> 4/4 Отключение входа по паролю (только по ключу)"
echo "    ПРОВЕРЬ во ВТОРОМ терминале: ssh -i <твой_ключ> root@<IP> — заходит?"
read -r -p "    Вход по ключу подтверждён и работает? Отключить пароль? [yes/NO]: " ANS
if [[ "$ANS" == "yes" ]]; then
  SSHD=/etc/ssh/sshd_config
  cp "$SSHD" "${SSHD}.bak.$(date +%s)"
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD"
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' "$SSHD"
  grep -q '^PasswordAuthentication' "$SSHD" || echo "PasswordAuthentication no" >> "$SSHD"
  # На новых системах настройки могут лежать в отдельных drop-in файлах:
  if ls /etc/ssh/sshd_config.d/*.conf >/dev/null 2>&1; then
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config.d/*.conf || true
  fi
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload
  echo "    Пароль отключён. НЕ закрывай текущую сессию, пока не убедишься, что новый вход по ключу работает."
else
  echo "    Пропущено. Отключишь позже вручную, когда убедишься в доступе по ключу."
fi

echo "==> Готово. Дальше: установи Docker и переходи к exit-узлу / стеку бота (см. README.md)."
