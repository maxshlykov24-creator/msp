# DKAcademy Analytics

Сервис аналитики продаж DKAcademy: тянет данные из amoCRM каждые 10 минут
(в окне 07:00–22:00 МСК), агрегирует и отдаёт интерактивный дашборд за
авторизацией на домене `dkacademy-analytics.ru`.

## Архитектура

- `api` (FastAPI) — дашборд, форма входа, JSON `/api/*`, `/health`.
- `worker` (APScheduler) — сбор из amoCRM по расписанию.
- `db` (PostgreSQL) — агрегаты (`snapshot`, `metric_daily`) и курсоры (`sync_state`).
- `nginx` (на хосте) — HTTPS + проксирование на `127.0.0.1:19090`.

Сбор изолирован от веба: падение одного контейнера не роняет другой.
`restart: unless-stopped` + healthcheck → автоподъём.

## Метрики из amoCRM (подтверждены в API)

Звонки и минуты (`call_*` + `duration`), новые лиды, когортная конверсия
лид→Успех, повторные продажи, выставленные счета по событию входа в этап и приход
по `closed_at + Успех`, средний чек, сообщения
(чат-события), застрявшие сделки (Без ответа 3 дня / Лист ожидания),
сделки без задач и просроченные (`tasks`), источники и причины провала.

Контроль качества CRM (`/api/data_quality`) отдельно показывает успешные сделки
с `price=0`, без контакта, источника, ответственного или корректного `closed_at`.
Привязка сообщений/времени ответа к менеджеру — по `responsible_user_id`.

## Локальный запуск (для разработки)

```bash
cp .env.example .env
# заполнить AMO_ACCESS_TOKEN, AUTH_PASSWORD_HASH, SESSION_SECRET
docker compose up -d --build
# дашборд: http://127.0.0.1:19090  (логин maxim)
```

Сгенерировать хеш пароля и секрет сессии:

```bash
python -c "import bcrypt;print(bcrypt.hashpw(b'Maxim123',bcrypt.gensalt()).decode())"
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

## Деплой на сервер (Timeweb VPS)

Предусловия: A-запись `dkacademy-analytics.ru` → IP сервера; добавлен
SSH-ключ деплоя; на сервере есть Docker + compose-plugin.

```bash
# 1) залить код (без .env)
rsync -avz --exclude '.env' --exclude '__pycache__' --exclude 'backups' \
  ./dkacademy-analytics/ root@SERVER:/root/dkacademy-analytics/

# 2) на сервере
cd /root/dkacademy-analytics
cp -n .env.example .env && nano .env       # токен, хеш пароля, секрет
bash deploy/up.sh                          # поднять контейнеры + health

# 3) домен + HTTPS
export FQDN=dkacademy-analytics.ru
bash deploy/nginx-domain.sh

# 4) бэкап в cron
( crontab -l 2>/dev/null; echo "0 3 * * * /root/dkacademy-analytics/deploy/backup.sh >> /var/log/dka-backup.log 2>&1" ) | crontab -
```

Проверка: `https://dkacademy-analytics.ru` → форма входа (maxim / Maxim123) → дашборд.

## Данные и период

Метрики делятся на два класса:

- **Потоковые** (выручка, лиды, конверсия, звонки, минуты, сообщения вх/исх,
  диалоги, пропущенные, счета, оплаты, повторные, время ответа, первый ответ,
  цикл) — хранятся подённо в `metric_daily` со `scope ∈ {all, mgr:<id>, src:<name>}`.
  API суммирует за выбранный период `?from=YYYY-MM-DD&to=YYYY-MM-DD`. Для средних
  и SLA хранятся суммы/счётчики (`rt_sum`/`rt_cnt`, `first_rt_*`, `cycle_*`,
  бакеты `rt_le5..rt_gt60`). Конверсия «лид → Успех» — когортная
  (`cohort_won/new_leads`, по дате создания лида). Конверсия повторных также
  когортная: `repeat_cohort_won/repeat_created`. Счета считаются по первому
  событию перехода на этап «Отправлен счёт».
- **Состояние «на текущий момент»** (воронки по этапам всего и по менеджерам,
  застрявшие, открытые/без задач/просроченные, причины провала, постоянные
  клиенты) — в `snapshot`, не зависят от периода.

Сбор: каждые 10 мин (07:00–22:00 МСК) обновляются состояние + окно лидов/звонков
за 14 дней + события за последние дни. События смены статуса адресно обновляют
старые когорты, поэтому поздний Успех через 90+ дней не теряется. Историю на 12 мес наполняет разовый
бэкфилл по месяцам (курсор `backfill_done_from` в `sync_state`); запуск вручную:
`docker compose exec -d worker python -m app.collector backfill`.

Приближения (помечены в UI): время ответа/сообщения по менеджеру — по
`responsible_user_id` (часть автосообщений Wazzup идёт с `created_by=0`); медиана
ответа — по бакетам; ~30% выигранных сделок с `price=0` (гигиена данных у клиента).

## Эндпоинты

| Путь | Назначение | Авторизация |
|------|------------|-------------|
| `GET /` | дашборд | да (редирект на /login) |
| `GET /login`, `POST /login` | форма входа | нет |
| `GET /logout` | выход | — |
| `GET /health` | мониторинг (status, db, data_age_min) | нет |
| `GET /api/overview\|series\|managers\|speed\|sources\|activity\|table` | потоковые (period: `?from&to`) | да |
| `GET /api/funnels\|stuck\|loss_reasons\|meta\|data_quality` | состояние «на момент» | да |
| `GET /api/status` | свежесть данных, курсор бэкфилла | да |

## Обслуживание

- Логи: `docker compose logs --tail=200 api worker`
- Перезапуск: `docker compose up -d`
- Свежесть данных: `curl https://dkacademy-analytics.ru/health` → `data_age_min`
- Токен amo действует до 2028 — обновить значение в `.env` и `docker compose up -d`.

## Независимая сверка

- Паспорт метрик: `МЕТРИКИ.md`.
- Регламент приёмки: `ПРОВЕРКА_ЦИФР.md`.
- Read-only расчёт напрямую из amoCRM: `scripts/audit_amo.py`.
- Клиентский HTML-протокол: `scripts/render_audit_report.py`.
- Тесты формул: `python -m unittest discover -s tests -v`.
