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

- `customerorder` — `CREATE`, `UPDATE`, `DELETE`
- `counterparty` — `UPDATE` (защита бонусных полей + приём ручных правок Уровня/Статуса)
- `bonustransaction` — `CREATE` (учёт ручных операций менеджера через документ Показателей)

**Timeweb + Tilda на одном хосте:** [deploy/TIMEWEB.md](deploy/TIMEWEB.md), `./deploy/timeweb-up.sh`, [docker-compose.timeweb.yml](docker-compose.timeweb.yml), [deploy/nginx/loyalty-same-host.example.conf](deploy/nginx/loyalty-same-host.example.conf).

Или зарегистрируйте из консоли (`WEBHOOK_PUBLIC_URL` и `MS_TOKEN` в `.env`):

```bash
export WEBHOOK_PUBLIC_URL="https://<домен>/webhook/moysklad"
python scripts/register_webhooks.py
```

Опционально: задайте `WEBHOOK_SECRET` в `.env` и на стороне прокси передавайте заголовок `X-Loyalty-Secret` (сам МойСклад такой заголовок не шлёт — только если терминация вручную/через nginx).

## Логика

| Блок | Поведение |
|------|-----------|
| Уровни | Знакомство / Дружба / Любовь; пороги 0 / 20k / 60k ₽ годовой оборот |
| Регистрация в ПЛ | Новый клиент **не регистрируется автоматически**. Регистрация только из заказа покупателя: менеджер ставит `Статус ПЛ = Активен` и выбирает `Уровень ПЛ` |
| Кэшбэк | Статус заказа **Доставлен** → `bonustransaction` EARNING, `executionDate` = now + `BONUS_DELAY_DAYS` |
| Возвраты | **Возврат** / **Частичный возврат** — отмена / коррекция начисления, **возврат списания** клиенту |
| Manual SPEND | Менеджер пишет «Списано бонусов» в заказе → cap = min(intent, balance, 30% от заказа). Идемпотентно: при изменении intent создаётся delta-`bonustransaction` (SPENDING или EARNING-обратка) |
| Field ownership | «Активно бонусов» (заказ), «Активные бонусы» / «Ожидают активации» (контрагент) — owner = система, ручные правки откатываются. «Уровень» — менеджер, **только повышение**. «Статус» — менеджер (Активен ↔ Заблокирован) |
| Заблокированный клиент | Нет начислений (cashback / welcome / birthday) и нет списаний, попытка → 0 в комментарий |
| Pending бонусы | `BonusBatch.activates_at` отделяет «Ожидают активации» от «Активные бонусы» (списание FIFO — только активные) |
| Ручные бонусы | `bonustransaction` CREATE (не наш `externalCode`) учитывается только для уже зарегистрированных участников ПЛ |
| Приветствие | При ручной регистрации из заказа начисляется `REGISTRATION_WELCOME_BONUS_POINTS` (по умолчанию 300) |
| День рождения | `BIRTHDAY_BONUS_POINTS` > 0 + заполнен `birth_month` / `birth_day` — ежедневный cron |
| Сверка / срок | reconciliation, tier resync (с уважением `tier_floor`), сгорание батчей |

### Защита от циклов

Все наши `PUT/POST/DELETE` идут с `X-Lognex-WebHook-Disable: 1` — МС не пришлёт обратный UPDATE по нашим же правкам. Параллельные обновления одного клиента сериализуются `pg_advisory_xact_lock`.

### Доп. поля МойСклад

- **Заказ покупателя** (5 полей): `Статус ПЛ`, `Уровень ПЛ`, `Активно бонусов`, `Списано бонусов`, `Комментарии ПЛ`.
- **Контрагент** (4 поля): `Уровень`, `Статус`, `Активные бонусы`, `Ожидают активации`.

UUID полей и бонусной программы:

```bash
MS_TOKEN=... python scripts/discover_metadata.py
```

Заполните соответствующие `ATTR_*` в `.env` и перезапустите сервис.

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
