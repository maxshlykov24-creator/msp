# Комбикорм-бот (Александр) — товароучёт в Telegram

Голосовой бот-товароучёт для точки Чечевилово. Александр наговаривает продажи,
приёмку, списание и инвентаризацию; Nexara переводит голос в текст; код разбирает
речь по номенклатуре; бот ведёт остаток и сверяет деньги с отчётом Виталика.

Telegram сейчас, MAX — позже через `MessengerAdapter` (ядро не меняется).

## Структура

```
bot/
├── app/
│   ├── main.py            # точка входа, запуск long polling
│   ├── config.py          # чтение .env
│   ├── db.py              # SQLite: схема, whitelist, движения, остаток
│   ├── catalog.py         # импорт номенклатуры из CSV → products+aliases
│   ├── pipeline.py        # текст → черновик позиций (нормализация + матчинг)
│   ├── operations.py      # продажи, приёмка, списание, инвентаризация, деньги
│   ├── ui.py              # тексты и клавиатуры (без привязки к мессенджеру)
│   ├── backup.py          # ежедневный бэкап БД (+ копия в Telegram)
│   ├── text/
│   │   ├── normalize.py   # число-слова, единицы, коды, разбор строки/денег
│   │   └── matcher.py     # матчинг по токенам + RapidFuzz
│   ├── stt/nexara.py      # клиент Nexara STT
│   └── adapters/
│       ├── base.py        # интерфейс мессенджера
│       └── telegram.py    # aiogram 3: меню, приём голоса, подтверждение, правки
├── scripts/
│   ├── build_catalog.py   # импорт CSV в БД + файл на утверждение
│   └── smoke.py           # смоук-тест ядра без Telegram
├── deploy/
│   ├── VPS_INVENTORY.md   # карта сервера (что уже крутится)
│   ├── kombikorm-bot.service
│   ├── deploy.sh          # изолированный деплой
│   └── migrate.sh         # перенос на новый сервер
├── requirements.txt
└── .env.example
```

## Локальный запуск

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env            # заполнить TELEGRAM_BOT_TOKEN, NEXARA_API_KEY
./.venv/bin/python -m scripts.build_catalog "/путь/Товары.csv" --replace
./.venv/bin/python -m app.main
```

## Каталог

`scripts/build_catalog.py` фильтрует активную розницу (`Статус = да`), строит короткие
имена и алиасы, кладёт в БД и пишет `catalog_review.md` на утверждение. Матчинг
дополнительно само-обучается: правки Александра сохраняются как новые алиасы.

## День 0 (первичные остатки)

До первых продаж один раз пройти «🧮 Инвентаризация» — наговорить фактический
остаток. Это станет базовой точкой (первые движения `inv_adj`).

## Доступ

Whitelist: первые 2 пользователя, нажавшие `/start` (владелец + Александр).
Остальные получают отказ. Хранится в таблице `allowed_users`.

## Деплой (VPS, изолированно)

Соседние боты (`keris-bot`) не затрагиваются — отдельный user/каталог/юнит.

```bash
HOST=213.165.44.164 ./deploy/deploy.sh
# затем на сервере заполнить /opt/kombikorm-bot/.env и:
ssh root@HOST 'systemctl restart kombikorm-bot && journalctl -u kombikorm-bot -f'
```

Перенос на новый сервер: `OLD=<ip> NEW=<ip> ./deploy/migrate.sh`.

## Nexara

Endpoint `POST https://api.nexara.ru/api/v1/audio/transcriptions`, `response_format=json`,
без диаризации. Ключ — из кабинета app.nexara.ru → API keys, кладётся в `.env`.
