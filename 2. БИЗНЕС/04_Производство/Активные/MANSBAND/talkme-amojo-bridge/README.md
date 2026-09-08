# Мост Talk-me ↔ amoCRM (Mansband)

**Уроки и типовые ошибки (для следующих клиентов):** [`../УРОКИ_ИНТЕГРАЦИИ_Talk-me_amoCRM.md`](../02_Знание/УРОКИ_ИНТЕГРАЦИИ_Talk-me_amoCRM.md).

## Быстрый старт (Docker)

1. Скопируйте `.env.example` → `.env`, заполните (см. комментарии).
2. `docker compose up -d --build`
3. Проверка: `curl -s http://127.0.0.1:19080/health`
4. На хосте: Nginx + HTTPS → проксирование на `127.0.0.1:19080` (см. `deploy/nginx.example.conf` и [`../MartaChe/loyalty-service/deploy/TIMEWEB.md`](../../MartaChe/loyalty-service/deploy/TIMEWEB.md)).
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
| POST | `/amo/webhooks/leads/{secret}` | Хук amoCRM «сделка добавлена» → метки визита в поля сделки |
| POST | `/internal/setup-lead-webhook` | Зарегистрировать хук `add_lead` в amoCRM (нужен `X-Internal-Secret`) |

## Метки визита (UTM, Roistat, yclid)

Talk-me отдаёт метки в `data.client`: `utm` объектом, `roistatVisitId`, `referer`, а `yclid` — в URL
входа `lastVisit.page.url`. Мост запоминает первое касание в `conversation_map.tracking`, а по хуку
`add_lead` находит сделку по телефону контакта и заполняет **пустые** поля типа `tracking_data`.
Поля ищутся по `code` (`UTM_SOURCE`, `ROISTAT`, `YCLID`…), поэтому переносится на другого клиента
без правок. Занятые поля не перезаписываются.

Разовый бэкфилл истории (по умолчанию только отчёт, без записи):

```bash
docker compose exec -T api python -m scripts.backfill_tracking
docker compose exec -T api python -m scripts.backfill_tracking --apply
```

**Talk-me → amo:** `conversation_id` в amo = `tm-{dialogId}`. **amo → Talk-me:** в теле к Talk-me сейчас `dialogId` + `message` (подредактируйте под [json-doc](https://lcab.talk-me.ru/cabinet/json-doc/online) в ЛК).  

## Зависимости от внешних данных

- ТП: **ID канала**, **секрет**, затем `connect` → **scope_id**.
- Talk-me: **REST токен** и корректный **путь/тело** отправки (настройка в `TALKME_SEND_MESSAGE_PATH` и при необходимости правка `send_text_to_visitor` в `app/talkme_client.py`).

## Локальные секреты Mansband (не в репо)

См. `2. БИЗНЕС/.../MANSBAND/_private/amo_talkme_bridge.env` в основном воркспейсе.
