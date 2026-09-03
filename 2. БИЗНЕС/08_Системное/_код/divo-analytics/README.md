# DIVO Motors Analytics

Сервис аналитики продаж DIVO Motors: каждые 10 минут тянет из amoCRM
обезличенные снимки сделок воронки «Продажи» + историю смены этапов,
**полностью пересобирает** дневные агрегаты и отдаёт дашборд за
авторизацией на домене `divomotors-analytics.ru`.

## Архитектура

- `api` (FastAPI) — дашборд, форма входа, `/api/daily`, `/api/meta`, `/health`.
- `worker` (APScheduler) — сбор из amoCRM по расписанию + бэкфилл истории этапов.
- `db` (PostgreSQL) — `lead_snapshot` (текущее состояние сделок), `stage_reached`
  (история этапов), `snapshot` (готовый JSON `DAILY`), `sync_state` (курсоры/ошибки).
- `nginx` (на хосте) — HTTPS + проксирование на `127.0.0.1:19100`.

Модель данных — **cohort vs outcomes** (см. `ПРОВЕРКА_ЦИФР_DIVO.md`):
- **cohort** (`traffic`, `active`, `agreed`, `tradein`, `src{}`, `stage{}`,
  `cohort_won`, `cohort_lost`) — по дате **создания** сделки, текущее состояние.
- **outcomes** (`won`, `lost`, `cash`, `invoice`, `tradein_won`, `loss{}`,
  `cycle_sum/n`) — по дате **закрытия** сделки (статус Успех/Провал).

Каждый прогон collector'а полностью пересобирает `DAILY` из хранимых снимков —
reopen, смена ответственного/источника/чекбоксов не оставляют «протухших»
цифр в старых днях.

## Схема-валидация перед каждым сбором

`app/collector.py::validate_schema()` перед КАЖДЫМ прогоном проверяет
`account_id`, воронку/статусы, ID менеджеров-пользователей и custom fields
(источник/оплата/трейд-ин/причина/согласовано) + обязательные enum-значения.
При несовпадении — collector не пишет данные, `/health` отдаёт
`schema_ok: false`, дашборд показывает баннер вместо тихого показа
устаревших цифр.

## Локальный запуск (для разработки)

```bash
cp .env.example .env
# заполнить AMO_ACCESS_TOKEN, AUTH_PASSWORD_HASH, SESSION_SECRET
# доп. логин: AUTH_USERS=login:$$2b$$12$$...  (не ломает основной AUTH_LOGIN)
# локально (http, без TLS) поставить AUTH_COOKIE_SECURE=false
docker compose up -d --build
# дашборд: http://127.0.0.1:19100
```

Сгенерировать хеш пароля и секрет сессии:

```bash
python -c "import bcrypt;print(bcrypt.hashpw(b'ПАРОЛЬ',bcrypt.gensalt()).decode())"
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Разовый полный бэкфилл истории этапов (после первого запуска, worker должен
быть остановлен на время бэкфилла):

```bash
docker compose stop worker
docker compose run --rm worker python -m app.collector backfill
docker compose start worker
```

## Деплой на сервер

Предусловия: A-запись `divomotors-analytics.ru` → IP сервера; на сервере
Docker + compose-plugin.

```bash
# 1) залить код (без .env)
rsync -avz --exclude '.env' --exclude '__pycache__' --exclude 'backups' \
  ./divo-analytics/ root@SERVER:/root/divo-analytics/

# 2) на сервере
cd /root/divo-analytics
cp -n .env.example .env && nano .env       # токен, хеш пароля, секрет, AUTH_COOKIE_SECURE=true
bash deploy/up.sh                          # поднять контейнеры + health
docker compose stop worker && docker compose run --rm worker python -m app.collector backfill && docker compose start worker

# 3) домен + HTTPS
export FQDN=divomotors-analytics.ru
bash deploy/nginx-domain.sh

# 4) бэкап в cron
( crontab -l 2>/dev/null; echo "0 3 * * * /root/divo-analytics/deploy/backup.sh >> /var/log/divo-backup.log 2>&1" ) | crontab -
```

Проверка: `https://divomotors-analytics.ru` → форма входа → дашборд.
Восстановление из бэкапа: `bash deploy/restore.sh backups/divoanalytics_*.sql.gz`.

## Эндпоинты

| Путь | Назначение | Авторизация |
|------|------------|-------------|
| `GET /` | дашборд | да (редирект на /login) |
| `GET /login`, `POST /login` | форма входа | нет |
| `GET /logout` | выход | — |
| `GET /health` | мониторинг (status, db, schema_ok, data_age_min, stale, last_error) | нет |
| `GET /api/daily` | плоский `DAILY[date]={...}` — контракт фронта | да |
| `GET /api/meta` | свежесть, надёжность исторической воронки, менеджеры | да |

## Обслуживание

- Логи: `docker compose logs --tail=200 api worker`
- Перезапуск: `docker compose up -d`
- Свежесть данных: `curl https://divomotors-analytics.ru/health`
- Токен amoCRM — см. exp в самом JWT; обновить значение в `.env` и `docker compose up -d`.
- Сверка цифр с amoCRM: `ПРОВЕРКА_ЦИФР_DIVO.md` в этой же папке проекта.
