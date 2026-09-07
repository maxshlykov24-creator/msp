#!/usr/bin/env bash
# Проверка токена МойСклад и сохранности локального кэша.
# Запуск на VPS: cd /opt/mansband-kassa && bash deploy/smoke-ms-token.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env ]]; then
  echo "ОШИБКА: отсутствует .env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${MOYSKLAD_API_BASE:?MOYSKLAD_API_BASE обязателен}"
: "${MOYSKLAD_API_TOKEN:?MOYSKLAD_API_TOKEN обязателен}"

api_code="$(
  curl -sS --compressed -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${MOYSKLAD_API_TOKEN}" \
    -H "Accept-Encoding: gzip" \
    "${MOYSKLAD_API_BASE}/context/employee"
)"
if [[ "$api_code" != "200" ]]; then
  echo "ОШИБКА: токен МойСклад не прошёл проверку (HTTP ${api_code})" >&2
  exit 1
fi

echo "МойСклад auth: OK"
echo "Кэш:"
docker compose exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "
    select '\''products'\'', count(*) from products
    union all select '\''stock'\'', count(*) from stock
    union all select '\''ms_refs'\'', count(*) from ms_refs
    union all select '\''deals_with_ms_order'\'', count(*) from deals where ms_order_id is not null;
  "'

hrefs="$(
  docker compose exec -T db sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "
      select ms_meta_href
      from products
      where ms_type in ('\''product'\'', '\''variant'\'')
      order by updated_at desc
      limit 3;
    "'
)"

checked=0
while IFS= read -r href; do
  [[ -z "$href" ]] && continue
  code="$(
    curl -sS --compressed -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer ${MOYSKLAD_API_TOKEN}" \
      -H "Accept-Encoding: gzip" \
      "$href"
  )"
  if [[ "$code" != "200" ]]; then
    echo "ОШИБКА: сохранённая сущность МойСклад недоступна (HTTP ${code})" >&2
    exit 1
  fi
  checked=$((checked + 1))
done <<< "$hrefs"

if [[ "$checked" -eq 0 ]]; then
  echo "ОШИБКА: в кэше нет позиций для spot-check" >&2
  exit 1
fi

echo "Сохранённые сущности: ${checked}/${checked} OK"
