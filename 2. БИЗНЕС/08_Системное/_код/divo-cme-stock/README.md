# DIVO CME → Google Sheet

Каждые 15 минут забирает сток из CM.Expert и пишет на лист **«Данные»**.
Sheet1 для виджета amo читает оттуда формулой. Шапка Sheet1 `A1:O1` — контракт, не трогать.

Таблица: [Автомобили | DIVO MOTORS](https://docs.google.com/spreadsheets/d/1icRyb3qObLM80b2S-zvwIttB808FIsDM8kB5Wu9u23Q/edit)

## Ключи API

Ключи получены 27.08.2026, OAuth живой (scope `full:dms`), cutover выполнен —
Sheet1 читает «Данные» формулой FILTER, бэкап `Sheet1_backup_2026-08-27`.
Ключи лежат **только** в `.env` на VPS. В git и vault не класть.

В аккаунте несколько салонов (DIVO 15371, Герман, Питер, Красногорск и тестовые) —
в таблицу идёт только салон DIVO: `CME_DEALER_ID=15371` в `.env` обязателен.

Если ключи отзовут: логин в ЛК CM.Expert **не** является `CLIENTID`/`CLIENTSECRET`,
запрашивать у `help@cm.expert` — готовый текст в
`2. БИЗНЕС/04_Производство/Активные/DIVO_Motors/02_Знание/2026-08-13_запрос_ключей_CME_API.md`.
Без ключей контейнер стартует, прогон пишет skip в лог, лист не затирает.

## Команды

```bash
cp .env.example .env          # вписать ключи CME, Telegram
cp "../../../04_Производство/Активные/MANSBAND/_private/google_sheets_sa.json" google_sa.json

python -m app.check_access    # доступ SA к таблице
python -m app.probe           # API без записи
python -m app.sync            # один боевой прогон → только «Данные»
python -m app.setup_sheet1    # dry-run формулы Sheet1
python -m app.setup_sheet1 --apply   # после сверки числа строк
python -m app.cutover                # sync + переключение Sheet1 одним шагом
```

## Деплой (VPS `104.171.136.226`, рядом с divo-analytics)

```bash
bash deploy/up.sh
# на сервере: nano /root/divo-cme-stock/.env
```

Секреты: `.env` + `google_sa.json` на сервере, не в git.
Состояние `last_ok` — docker volume `cme_state`.

## Страховки

- Ошибка API / пустой ответ / обвал >50% к прошлому успеху → **не писать**.
- Запись: update N строк, затем clear хвоста (не наоборот).
- Пустой склад пишется только при `ALLOW_EMPTY=1`.
- Три неуспеха подряд → Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).

## Фильтр публикации

По умолчанию `CME_PUBLISH_MODE=require`: без поля статуса публикации в JSON
в таблицу не пишем. После `python -m app.probe` либо ставим
`CME_PUBLISH_FIELD=...`, либо (если поля нет в API) `CME_PUBLISH_MODE=in_stock_only`.
