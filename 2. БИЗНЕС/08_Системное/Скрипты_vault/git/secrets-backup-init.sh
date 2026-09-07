#!/bin/sh
# Первичная настройка бэкапа доступов: парольная фраза в Keychain.
#
# Фраза генерируется случайно и в чат, в git и в логи не попадает.
# После запуска её нужно один раз скопировать в приложение «Пароли» —
# оттуда она уедет в iCloud и будет доступна с iPhone. Без копии вне этого Mac
# бэкап бесполезен: диск умрёт вместе с Keychain, и архивы не расшифровать.

set -e

service="vault-secrets"
account="$USER"

if security find-generic-password -s "$service" >/dev/null 2>&1; then
  echo "Запись '$service' в Keychain уже есть. Ничего не меняю."
  echo "Посмотреть фразу:  security find-generic-password -s $service -w"
  echo "Заменить принудительно:  security delete-generic-password -s $service && $0"
  exit 0
fi

pass=$(openssl rand -base64 30 | tr -d '\n')

# -w передаёт фразу аргументом: она на доли секунды видна в списке процессов.
# На локальной машине это приемлемо, интерактивного ввода тут быть не может —
# скрипт задуман неинтерактивным.
security add-generic-password \
  -s "$service" \
  -a "$account" \
  -D "vault secrets backup" \
  -j "Парольная фраза шифрованных бэкапов ДОСТУПЫ*.md и .env. Копия — в приложении «Пароли»." \
  -w "$pass" >/dev/null

unset pass

cat <<'TXT'
Готово: парольная фраза создана и лежит в Keychain.

Осталось одно действие руками, его нельзя автоматизировать:

  1. Покажи фразу:      security find-generic-password -s vault-secrets -w
  2. Открой приложение «Пароли» (Spotlight → Пароли)
  3. Создай запись «vault-secrets» и вставь фразу в поле пароля

После этого фраза окажется в iCloud и будет доступна с iPhone. Пока копии вне
этого Mac нет, бэкап доступов защищает только от случайного удаления файлов,
но не от смерти диска.
TXT
