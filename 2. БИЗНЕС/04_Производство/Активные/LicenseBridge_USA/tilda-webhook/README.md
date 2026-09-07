# LicenseBridge Hub (распределение + антидубли, v3 без NOVA)

Эволюция прежнего `tilda-webhook` в модульный **FastAPI + PostgreSQL** hub. Реализует
план `распределение_и_антидубли_licensebridge_(v3,_без_nova)`: предотвращение и
устранение дублей контактов/сделок через Kommo API (без платных виджетов), плюс
распределение новых лидов.

## Архитектура

```
Каналы (Tilda, Pleep, Kommo webhooks)  →  api (FastAPI)  →  inbox (PostgreSQL)  →  worker  →  Kommo API
                                                                                    ├─ dedup контактов/сделок
                                                                                    ├─ распределение (Илона)
                                                                                    └─ scanner (детект + возврат тегов)
```

- **api** отвечает `202`/`ok` за <2с, только кладёт событие в `inbox` (идемпотентно по `event_key`).
- **worker** берёт события, держит advisory-лок по телефону, делает всю работу с Kommo,
  ведёт журнал решений и JSON-снимки перед удалением; после N попыток — dead-letter.
- **scanner** (внутри worker, APScheduler): плановый детект дублей + еженедельный
  авто-возврат тегов пула `mc_*`/`md_*`.

## Модули `app/`

| Файл | Назначение |
|------|-----------|
| `config.py` | ID Kommo, ответственные, feature-flags, пороги |
| `kommo/client.py` | Kommo API v4: rate limit, retry, ошибки |
| `identity.py` | нормализация телефона E.164 + вторичные ключи (id мессенджеров Wazzup, Teletype, ttad) |
| `rollout.py` | авто-раскатка этапов feature-flags с health-gate |
| `chat.py` | чтение/классификация чата: none / light / rich |
| `dedup_contacts.py` | soft-merge / лёгкий чат / тег ручной склейки |
| `dedup_deals.py` | перенос примечаний + удаление; межворонка при создании |
| `assignment.py` | распределение (Илона, наследование от закрытой) |
| `merge_tags.py` | пул тегов `mc_NN`/`md_NN` (аллокация/возврат) |
| `intake.py` | единый приём Tilda/Pleep, предотвращение дублей до записи |
| `handoff.py` | won в Pipeline → новая сделка в «Сборке» с backdate `created_at` (сохраняет чаты/звонки в ленте), доп.поля, примечания, тег `сборка_из_продажи` |
| `leadflow.py` | лид-машина: вариант текста WhatsApp, таймзона клиента, AI-звонок Pleep, разбор итога, ветка SMS |
| `ami.py` | одна команда Originate на Asterisk (мост клиент ↔ голосовой агент) |
| `webhooks.py` | роуты: `/tilda/webhook`, `/pleep/sync`, `/kommo/webhook/{secret}`, `/status`, `/internal/rollout`, `/internal/responsible/resolve`, `/internal/handoff`, `/internal/leadflow/ai-call`, `/internal/pleep/outcome` |
| `resolver.py` | read-only резолвер ответственного для телефонии |
| `scanner.py` | детект дублей + авто-возврат тегов |
| `worker.py` | цикл обработки inbox + диспетчер событий |
| `preflight.py` | сверка ID с реальным аккаунтом до мутаций |

## Запуск (сервер Timeweb 72.56.123.137)

```bash
cd /opt/licensebridge-tilda-webhook
cp .env.example .env   # заполнить секреты, chmod 600
docker compose up -d --build
docker compose ps
curl -sS http://127.0.0.1:8080/health
```

Или деплой скриптом (с локальной машины; секреты берутся из `tilda-webhook/.env`,
SSH — по ключу `~/.ssh/licensebridge_hub_deploy`, иначе `DEPLOY_SSH_PASSWORD`):

```bash
python3 scripts/deploy.py
```

## Скрипты

| Скрипт | Назначение |
|--------|-----------|
| `scripts/deploy.py` | деплой на VPS по SSH-ключу, секреты из локального `.env` |
| `scripts/register_kommo_webhook.py` | идемпотентная подписка на нативные вебхуки Kommo |
| `scripts/rehearsal.py` | репетиция на боевой базе: прогнать все группы дублей через хаб и показать вердикты (см. RUNBOOK §2а) |

## Авто-раскатка вместо ручных флагов

`AUTO_ROLLOUT=true`: сервер сам идёт по этапам, каждый следующий — только если
выдержано время и нет сбоев (dead-letter, затор очереди, отказ preflight).

```
0 shadow (24 ч, ≥20 решений) → 1 assignment → 2 deals → 3 contacts_no_chat → 4 contacts_light
```

В shadow входящие Tilda/Pleep-лиды всё равно создаются (`INTAKE_CREATE_IN_SHADOW=true`),
чтобы не терять заявки. Наблюдение — `GET /status?key=<INTERNAL_API_KEY>`; пауза,
возврат в shadow и ручная установка этапа — `POST /internal/rollout` (см. RUNBOOK).
Ручной режим: `AUTO_ROLLOUT=false` + `SHADOW_MODE`/`ENABLE_*`.

Насыщенные чаты, вложения, Instagram-ник без телефона — **всегда** только тег на
ручную native-merge Kommo, без удаления.

## Handoff Pipeline → Сборка (вне авто-раскатки)

`ENABLE_HANDOFF=false` по умолчанию — изменение бизнес-процесса (won перестаёт
«зависать» в Pipeline ради истории), включается вручную после подтверждения
владельца и smoke-теста на боевом (RUNBOOK §2в). Пока выключен — только
`log_decision("handoff.shadow", …)`, сделки не создаются. Подробности механизма
(backdate `created_at` для видимости чатов/звонков в ленте, идемпотентность,
защита от петли) — в `app/handoff.py` и в плане `handoff_pipeline_to_sborka`.

## Лид-машина WhatsApp → AI-звонок → SMS (вне авто-раскатки)

`ENABLE_LEADFLOW`, как и телефония, включается руками. Что делает хаб:

| Событие | Что происходит |
|---|---|
| `add_lead` в Pipeline | в сделку пишутся `wa_variant` (1–4) и окно звонка по таймзоне номера |
| `POST /internal/leadflow/ai-call` | Salesbot не дождался ответа в WhatsApp: если у клиента тихие часы — звонок ждёт 09:00 по его времени, иначе AMI Originate моста клиент ↔ Pleep |
| отчёт Asterisk с `branch=ai` | клиент не взял трубку → сразу ветка SMS, ждать итога от Pleep незачем |
| `POST /internal/pleep/outcome` | `qualified` → этап «В работе» + задача; `callback` → задача; `refused`/`no_answer` → SMS-бот; `do_not_call` → тег «не звонить» |

Один клиент — один AI-звонок: таблица `ai_calls` уникальна по `lead_id`, повтор
вебхука Salesbot или ретрай HTTP-tool Pleep ничего не дублируют. Полное описание
схемы, ТЗ на ботов и порядок включения — `../2026-08-10_ЛИД-МАШИНА_WhatsApp_Pleep_SMS.md`.

## Тесты

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest   # локально psycopg2 не обязателен (тесты на sqlite)
pytest -q
```

## Важно: ограничения API Kommo

- **NOVA дальше не используется.** Вся логика «вперёд» — здесь.
- **Merge через API не существует** → контакты с перепиской клеим вручную (native-merge
  в интерфейсе, история сохраняется), остальные — soft-merge (перелинковка сделок,
  перенос полей и сообщений).
- **Удаления сущностей в API тоже нет**: `DELETE /leads/{id}` и `/contacts/{id}` отдают
  `405` (политика платформы, проверено на боевом аккаунте 2026-07-24). Поэтому дубль
  сначала обезвреживается (работа перенесена на основную карточку), затем помечается
  тегом `дубль_удалить`; при заданном `ARCHIVE_PIPELINE_ID` сделка-дубль ещё и
  уезжает в архивную воронку. Физически удаляет человек — пакетно по фильтру тега.
- **Привязка контакта к сделке** — `metadata.is_main`, а не `main_contact`: последнее
  API отвергает (`400 FieldNotExpected`).
- **Поиск по телефону нечёткий и форматозависимый**: один номер лежит в базе как
  `7473361387`, `17473361387` и `+18188252213`; query матчит подстроку цифр, поэтому
  ищем по национальным 10 цифрам (`identity.phone_query_variants`).
- **Сообщения мессенджеров в карточках отсутствуют**: Wazzup не пишет их в примечания
  Kommo (в базе 0 заметок `amomessage`), создать такую заметку через API нельзя.
  Классификация чата опирается на звонки/вложения — они уводят пару в ручную склейку.
- **Переписку отдаёт только общий поток событий**: `with=talks` на сделке пусто, а
  `GET /events?filter[entity_id]=<сделка>` чат-события не возвращает вовсе (19.08.2026:
  по сделке 29892373 — 31 событие и ни одного чат-события при 21 сообщении клиента).
  Считать переписку можно лишь окном `GET /events?filter[type]=…` по всему аккаунту с
  группировкой по сделке — `app/chat_events.py`. Текстов сообщений API v4 не отдаёт:
  только факт, канал, автор и время.
- **Автор примечания-звонка — только `created_by`**: без него Kommo подписывает звонок
  пользователем интеграции, и в карточке выглядит, будто клиенту звонил владелец
  аккаунта. Ставится по добавочному из диалплана (`ext` → `TELEPHONY_EXT_USERS`).
  Id уволенного пользователя в `created_by`/`responsible_user_id` Kommo отвергает
  целиком (`400 NotSupportedChoice`) — карту добавочных править при смене менеджера.
