#!/bin/sh
# Шифрованный бэкап доступов в iCloud Drive.
#
# Зачем: файлы `ДОСТУПЫ*.md` и `.env` намеренно исключены из git — и потому не
# имеют ни одной копии. Time Machine на этом Mac не настроен (`tmutil` 07.09.2026:
# "No destinations configured"). Смерть диска означала бы потерю паролей к
# клиентским кабинетам: они существуют в единственном экземпляре.
#
# Что делает: собирает файлы в tar.gz, шифрует AES-256 и кладёт в iCloud Drive
# с датой в имени. Хранит последние 14 копий. Пароль берёт из Keychain.
#
# Первый запуск: сначала `secrets-backup-init.sh` — он создаёт парольную фразу.
# Восстановление: см. `_СИСТЕМА/СИНХРОНИЗАЦИЯ.md`, раздел «Доступы».

set -e

root=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$root"

log_dir="$root/2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs"
log_file="$log_dir/secrets-backup.log"
dest="$HOME/Library/Mobile Documents/com~apple~CloudDocs/vault-secrets"
keep=14
service="vault-secrets"

mkdir -p "$log_dir"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$log_file"
}

fail() {
  log "error: $*"
  # Молчаливый провал недопустим: у этих файлов других копий нет.
  osascript -e "display notification \"Бэкап доступов не удался: $*\" with title \"Vault secrets\"" 2>/dev/null || true
  exit 1
}

# Пароль только из Keychain: в git и в аргументах команд он не появляется.
pass=$(security find-generic-password -s "$service" -w 2>/dev/null) \
  || fail "в Keychain нет записи '$service' — запусти secrets-backup-init.sh"
[ -n "$pass" ] || fail "парольная фраза пустая"

[ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ] \
  || fail "iCloud Drive недоступен"
mkdir -p "$dest"

work=$(mktemp -d) || fail "не удалось создать временный каталог"
chmod 700 "$work"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT INT TERM

# Список файлов. Исключаем чужое (node_modules, .venv) и служебное.
find . \( -name 'ДОСТУПЫ*.md' -o -name '.env' \) \
  -not -path './.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/.venv/*' \
  -type f \
  >"$work/list" 2>/dev/null || true

count=$(wc -l <"$work/list" | tr -d ' ')
[ "$count" -gt 0 ] || fail "не найдено ни одного файла доступов — проверь, не переехали ли они"

# Манифест с контрольными суммами: по нему проверяется восстановление.
{
  printf '# Бэкап доступов vault\n'
  printf '# Снят: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  printf '# Файлов: %s\n\n' "$count"
  while IFS= read -r f; do
    shasum -a 256 "$f"
  done <"$work/list"
} >"$work/МАНИФЕСТ.txt"

# Сборка в два шага. BSD tar не берёт список файлов (-T) и добавочный файл из
# другого каталога (-C) в одном вызове, а к готовому .tar.gz дописать нельзя —
# поэтому сначала несжатый tar, потом манифест, потом gzip.
tar cf "$work/secrets.tar" -T "$work/list" 2>/dev/null \
  || fail "не удалось собрать архив"
tar rf "$work/secrets.tar" -C "$work" "МАНИФЕСТ.txt" 2>/dev/null \
  || fail "не удалось добавить манифест в архив"

archive="$work/secrets.tar.gz"
gzip -c "$work/secrets.tar" >"$archive" || fail "не удалось сжать архив"

# Пароль отдаём через файл с правами 600 внутри каталога 700: в списке процессов
# его не видно, в отличие от -pass pass:...
pwfile="$work/pw"
printf '%s' "$pass" >"$pwfile"
chmod 600 "$pwfile"

stamp=$(date '+%Y-%m-%d_%H%M')
out="$dest/secrets-$stamp.tar.gz.enc"

openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
  -in "$archive" -out "$out" -pass file:"$pwfile" \
  || fail "шифрование не удалось"

# Проверка: расшифровываем обратно и смотрим, что архив читается и полон.
check=$(openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
  -in "$out" -pass file:"$pwfile" 2>/dev/null | tar tz 2>/dev/null | wc -l | tr -d ' ')
if [ "$check" -lt "$count" ]; then
  rm -f "$out"
  fail "проверка архива не прошла: в нём $check записей вместо $((count + 1))"
fi

size=$(du -k "$out" | awk '{print $1}')
log "ok: $count файлов, ${size} KB -> $(basename "$out"), проверка пройдена"

# Ротация: держим последние $keep копий.
old=$(ls -1t "$dest"/secrets-*.tar.gz.enc 2>/dev/null | tail -n +$((keep + 1)) || true)
if [ -n "$old" ]; then
  printf '%s\n' "$old" | while IFS= read -r f; do
    rm -f "$f" && log "ротация: снят $(basename "$f")"
  done
fi
