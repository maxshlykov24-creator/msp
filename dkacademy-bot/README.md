# DKAcademy-bot: Telegram onboarding + LiveInform → amoCRM + уведомления

Сервис на **FastAPI** + **aiogram 3** (long polling). По вебхуку LiveInform дергает `POST /api/v2/track/`, обновляет в amoCRM поля **«Статус LiveInform»** по совпадению **«Номер СДЭК»** с трек-номером из ответа; при возможности дописывает то же поле элементам каталога (режим `AMO_ORDER_ENTITY=catalog`). Отправляет текст клиентам в Telegram, если контакт есть в локальной таблице привязок.

Подробнее по логике — см. план DKAcademy в `.cursor/plans/` (раздел amo + LiveInform).

**Для ассистента:** переносимый опыт по LiveInform/amoCRM/Telegram и деплою — [AGENT_LEARNINGS.md](AGENT_LEARNINGS.md).

## Запуск локально с Docker Compose

```bash
cd dkacademy-bot
cp .env.example .env
# Заполнить .env: Telegram, amo, LiveInform, field_id
docker compose up -d --build
```

API: `http://127.0.0.1:19082/health`.

## amoCRM OAuth

1. В маркете завести интеграцию, прописать `REDIRECT_URI` на свой хост, например `https://your-host/oauth/callback`.
2. Открыть `GET https://your-host/internal/oauth-link` и пройти по ссылке (или собрать URL вручную из ответа).
3. После редиректа токены окажутся в БД (`amo_oauth_token`). Альтернатива MVP: указать только `AMO_LONG_LIVED_TOKEN` в `.env`.

## Поля в аккаунте DKAcademy

Обязательно завести в amo и подставить `field_id` в `.env`:

- В **сделке**: «Номер СДЭК», «Статус LiveInform».
- В **контакте** (минимум): поле текста под `telegram_chat_id`, поле даты или текста для отметки «бот запущен» (можно только дату через `AMO_FIELD_CONTACT_BOT_STARTED_AT`).

При использовании **каталога** как «заказ покупателя»: `AMO_ORDER_ENTITY=catalog`, плюс `AMO_CATALOG_ID` и `field_id` для трека и статуса там же.

Числовые id полей можно смотреть через GET `/api/v4/leads/custom_fields` или в настройках воронки.

## LiveInform

- Документация: [https://liveinform.ru/integration](https://liveinform.ru/integration).
- Зарегистрировать webhook на `POST https://your-host/webhooks/liveinform`.
- В кабинете задать секрет и указать заголовок, совпадающий с `LIVEINFORM_WEBHOOK_SECRET` и `LIVEINFORM_WEBHOOK_HEADER`.

Тело webhook должно содержать `liveinform_id` (или — после первого успешного `track` мы сохраняем связь tracking → id в таблице `liveinform_tracking_map`; при только треке в webhook нужно предварительно заполнить эту связь через API `add` и ваш обработчик, либо прислать id в теле webhook).

После каждого срабатывания выполняется **pull** статуса через `track`; в CRM пишется сводная строка (русские подписи 0–3 + последнее событие).

## Полезные ручки

| Метод | Путь | Назначение |
|--------|------|------------|
| GET | `/health` | Проверка процесса |
| GET | `/oauth/callback` | Редирект amo после OAuth |
| GET | `/internal/oauth-link` | HTML со ссылкой авторизации |
| POST | `/webhooks/liveinform` | Вход LiveInform |

## Nginx

См. [deploy/nginx.example.conf](deploy/nginx.example.conf) — минимальный прокси на `127.0.0.1:19082`.

**Прод на Timeweb VPS:** полный чеклист, скрипты `deploy/timeweb-up.sh` и `deploy/timeweb-nginx-subdomain.sh` — [deploy/TIMEWEB.md](deploy/TIMEWEB.md).

## Чеклист перед продом

- [ ] Ротация `LIVEINFORM_API_ID`, если ключ светился вне сервера.
- [ ] Ротация `TELEGRAM_BOT_TOKEN`, если светился.
- [ ] Реальный JSON примера webhook LiveInform (ключи `liveinform_id` / `tracking`).
- [ ] Проверка поиска сделки по «Номер СДЭК» на тестовом треке.
- [ ] Уточнить, нужен ли второй объект (каталог) вместо одной сделки.
