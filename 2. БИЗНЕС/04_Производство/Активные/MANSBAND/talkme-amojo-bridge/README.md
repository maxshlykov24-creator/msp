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
| POST | `/webhooks/quiz/{secret}` | Заявка квиза `zamer.mansband.ru` → сделка в «Продажи» |
| POST | `/amo/webhooks/leads/{secret}` | Хук amoCRM «сделка добавлена» → метки визита в поля сделки |
| POST | `/internal/setup-lead-webhook` | Зарегистрировать хук `add_lead` в amoCRM (нужен `X-Internal-Secret`) |
| GET | `/ct.js` | Скрипт подмены номера. Пока `CT_LIVE=false`, работает только с `?ct=1` |
| POST | `/ct/session` | Выдача номера из пула или статичного номера органики |
| POST | `/ct/ping` | Продление брони номера |
| POST | `/webhooks/telphin/{secret}` | Входящий звонок Телфин, сделку не создаёт |

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

## Напоминание о платной отложке

Отдельный сервис Compose `reminders`, `app/reservation_reminders.py`, порт
`127.0.0.1:19081`. Работает с той же БД, не меняет обработчики чата и квиза.
Ключ Wazzup и настройки сервиса лежат только на сервере в `.env.reminders`
(права `600`, исключён из Git и Docker build context).

Правило владельца от 22.10.2026: все открытые сделки всех воронок; поле
`Отложка` (`3810737`) = `Платная` (`5452541`), дата `Отложка до` (`3810739`).
Напоминание за три календарных дня, с 10:00 МСК. Рабочее окно до 20:00 МСК;
при перезапуске ночью письма/сообщения не догоняются. Просроченные дни при
первом запуске пропускаются. Старого робота с таким напоминанием, по ответу
владельца, нет.

Каждому каналу одна попытка: Telegram → WhatsApp → MAX. После ответа API
`201` ожидаем статус Wazzup. `sent`, `delivered`, `read` завершают цепочку;
подтверждённый отказ разрешает следующий канал. Неизвестный результат или
таймаут останавливает цепочку для проверки. Неоплаченные/неактивные каналы и
отсутствующий адрес пропускаются. Локальная уникальность попытки и фиксация
до POST защищают от повторов после сбоя дольше, чем окно `crmMessageId` Wazzup.
Ключ напоминания: сделка + дата окончания отложки. Перед отправкой условия и
основной контакт перечитываются. Неоднозначный контакт/телефон блокирует отправку.

В аккаунте amoCRM недоступен фильтр дополнительных полей (`Invalid filter for
current account`), поэтому раз в 15 минут полностью читаются открытые этапы,
а очередь проверяется каждые 30 секунд. Перед отправкой сделка читается заново.

Wazzup v3 поддерживает один внешний получатель событий. Новый маршрут
`/automation/wazzup/{secret}` пересылает исходное тело и Authorization в прежний
Integracio и возвращает его ответ. URL прежнего получателя и исходная подписка
сохранены на сервере в `/root/msp-backups/reservation-reminders-20261022/`.
В БД остаются только идентификаторы/статусы событий, не входящие тексты клиентов.
Доступ к `/internal/status` — только через localhost с `X-Reminder-Key`.
Секретный путь не пишется в access log.

Режим по умолчанию — `preview`. Для `live` обязательны `REMINDER_WEBHOOK_READY`,
`REMINDER_LEGACY_CHECKED`, ключ и все три канала. Настройки времени:
`REMINDER_HOUR=10`, `REMINDER_END_HOUR=20`, `REMINDER_DAYS_BEFORE=3`.
Остановка отправок: `REMINDER_MODE=preview` и пересоздание только `reminders`.
Сам сервис при этом продолжает пересылать события прежней интеграции.
Не останавливать его, пока webhook Wazzup не возвращён на сохранённый URL.

Проверки: `python3 -m pytest tests/test_reservation_reminders.py tests/test_quiz_lead.py
tests/test_calltrack.py -q`. Отрицательный `lead_id` в журнале — изолированный
тест на диалоге владельца, не сделка amoCRM.
