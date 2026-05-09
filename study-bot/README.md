# Study bot (Модуль 6, Nexara)

Локальный Telegram-бот: сохраняет текст, файлы, фото, транскрибирует голосовые и видео через [Nexara](https://docs.nexara.ru/guides). Исходные учебные материалы **не** меняются: эталон в [`Материалы_для_ученика 3/`](../Материалы_для_ученика%203/).

## Быстрый старт

```bash
cd study-bot
python3 -m pip install -r requirements.txt
python3 bot.py
```

Первый `/start` в Telegram фиксирует **владельца** в `Распределение/admin.json`; остальные пользователи отклоняются.

После клона репозитория, если у тебя нет `config.py`:

```bash
cp config_example.py config.py
# отредактируй ключи TELEGRAM_BOT_TOKEN и NEXARA_API_KEY
```

## Секреты

- `config.py` — рабочий файл с ключами (в `.gitignore`). Шаблон без секретов: `config_example.py`.
- Через окружение (опционально): `TELEGRAM_BOT_TOKEN`, `NEXARA_API_KEY` — см. `.env.example`.

## Куда пишутся файлы

Каталог `Распределение/` рядом с проектом:

- `сообщения/` — текст
- `транскрипты/` — расшифровки голосовых и видео
- `документы/`, `изображения/` — вложения
- `bot.log`, `processed.json`

## Безопасность

Если токены попадали в открытый чат — после проверки отзови их: `@BotFather` → `/revoke` для бота; в кабинете Nexara — перевыпусти ключ. Обнови `config.py` или `.env`.

## Документация API

- [Telegram Bot API](https://core.telegram.org/bots/api) (обзор платформы: [core.telegram.org](https://core.telegram.org/))
- [Nexara — быстрый старт](https://docs.nexara.ru/guides)
