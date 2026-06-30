# MartaChe — сервис лояльности для МойСклад

FastAPI + PostgreSQL: вебхуки заказов (статусы **Доставлен** / **Возврат** / **Частичный возврат**), отложенное начисление бонусов через `bonustransaction`, лог `bonus_log`, сверка и планировщик.

## Быстрый старт (Docker)

```bash
cd loyalty-service
cp .env.example .env
# Заполните MS_TOKEN и при необходимости DATABASE_URL
docker compose up -d --build
```

- API: `http://localhost:8080`
- Health: `GET /health`
- Вебхук МойСклад: `POST /webhook/moysklad` (формат уведомлений JSON API 1.2)
- Лог по заказу: `GET /api/log?order=<id_или_номер>`
- Meta бонусной программы: `GET /internal/meta/bonusprogram`

## Переменные окружения

См. [.env.example](.env.example). UUID доп. полей контрагента можно получить:

```bash
export MS_TOKEN="..."
python scripts/discover_metadata.py
```

Если `ATTR_*` не заданы, начисления и уровни всё равно ведутся в локальной БД (`loyalty_members`), синхронизация в карточку контрагента в МС пропускается.

## Важно по безопасности

- Файл `.env` с токеном **не коммитьте** (в `.gitignore`).
- Если токен когда‑либо светился в переписке — перевыпустите его в МойСклад.

## Настройка вебхука в МойСклад

Создайте вебхуки на `https://<ваш-домен>/webhook/moysklad` для сущностей:

- `customerorder` — `UPDATE`
- `bonustransaction` — `CREATE` (учёт ручных операций менеджера)

**Timeweb + Tilda на одном хосте:** [deploy/TIMEWEB.md](deploy/TIMEWEB.md), `./deploy/timeweb-up.sh`, [docker-compose.timeweb.yml](docker-compose.timeweb.yml), [deploy/nginx/loyalty-same-host.example.conf](deploy/nginx/loyalty-same-host.example.conf).

Или зарегистрируйте из консоли (`WEBHOOK_PUBLIC_URL` и `MS_TOKEN` в `.env`):

```bash
export WEBHOOK_PUBLIC_URL="https://<домен>/webhook/moysklad"
python scripts/register_webhooks.py
```

Опционально: задайте `WEBHOOK_SECRET` в `.env` и на стороне прокси передавайте заголовок `X-Loyalty-Secret` (сам МойСклад такой заголовок не шлёт — только если терминация вручную/через nginx).

## Логика (план)

| Блок | Поведение |
|------|-----------|
| Уровни | Знакомство / Дружба / Любовь; пороги 0 / 20k / 60k ₽ годовой оборот (без аутлета, подарков, доставки, с учётом бонусной оплаты) |
| Кэшбэк | Статус заказа **Доставлен** → `bonustransaction` EARNING, `executionDate` = now + `BONUS_DELAY_DAYS` |
| Возвраты | **Возврат** / **Частичный возврат** — отмена или коррекция бонусов, пересчёт `annual_sum_rub` |
| Ручные бонусы | `bonustransaction` CREATE (не `externalCode` сервиса) → sync батчей / FIFO |
| Приветствие | если `WELCOME_BONUS_POINTS` задан и больше 0: при **первой** доставке — отдельное начисление |
| День рождения | если `BIRTHDAY_BONUS_POINTS` больше 0 и заданы `birth_month` / `birth_day` — ежедневно по cron (см. `scheduler`), раз в год; заполнение ДР: импорт CSV или `scripts/sync_birthdays_from_moysklad.py` + `ATTR_BIRTHDATE` |
| Сверка / срок | Планировщик: reconciliation, tier resync, сгорание батчей |

## Локальный запуск без Docker

Поднимите PostgreSQL (порт в `docker-compose.yml` для `db` проброшен как `5433`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg2://loyalty:loyalty@localhost:5433/loyalty
export MS_TOKEN=...
uvicorn app.main:app --reload --port 8080
```
