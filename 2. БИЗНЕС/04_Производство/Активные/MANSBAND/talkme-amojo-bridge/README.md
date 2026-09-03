# Мост Talk-me ↔ amoCRM (Mansband)

**Уроки и типовые ошибки (для следующих клиентов):** [`../УРОКИ_ИНТЕГРАЦИИ_Talk-me_amoCRM.md`](../УРОКИ_ИНТЕГРАЦИИ_Talk-me_amoCRM.md).

## Быстрый старт (Docker)

1. Скопируйте `.env.example` → `.env`, заполните (см. комментарии).
2. `docker compose up -d --build`
3. Проверка: `curl -s http://127.0.0.1:19080/health`
4. На хосте: Nginx + HTTPS → проксирование на `127.0.0.1:19080` (см. `deploy/nginx.example.conf` и [Бизнес/04_Производство_внедрений/Активные/MartaChe/loyalty-service/deploy/TIMEWEB.md](../Бизнес/04_Производство_внедрений/Активные/MartaChe/loyalty-service/deploy/TIMEWEB.md)).
5. OAuth: в браузере откройте `GET /internal/oauth-link` на вашем FQDN (там ссылка на amo).
6. После ответа ТП с **channel_id** / **channel_secret** — `POST /internal/connect` с заголовком `X-Internal-Secret` (тот же, что `INTERNAL_SECRET` в `.env`).

## Маршруты

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/health` | Health |
| GET | `/oauth/callback` | Редирект amo (code → токены в БД) |
| GET | `/internal/oauth-link` | HTML с ссылкой на авторизацию |
| POST | `/internal/connect` | `connect` к аккаунту (нужен `X-Internal-Secret`) |
| POST | `/amojo/v2/hooks/{scope_id}` | Входящие из amo (проверка `X-Signature`) |
| POST | `/webhooks/talkme` | Входящие из Talk-me |

**Talk-me → amo:** `conversation_id` в amo = `tm-{dialogId}`. **amo → Talk-me:** в теле к Talk-me сейчас `dialogId` + `message` (подредактируйте под [json-doc](https://lcab.talk-me.ru/cabinet/json-doc/online) в ЛК).  

## Зависимости от внешних данных

- ТП: **ID канала**, **секрет**, затем `connect` → **scope_id**.
- Talk-me: **REST токен** и корректный **путь/тело** отправки (настройка в `TALKME_SEND_MESSAGE_PATH` и при необходимости правка `send_text_to_visitor` в `app/talkme_client.py`).

## Локальные секреты Mansband (не в репо)

См. `Бизнес/.../MANSBAND/_private/amo_talkme_bridge.env` в основном воркспейсе.
