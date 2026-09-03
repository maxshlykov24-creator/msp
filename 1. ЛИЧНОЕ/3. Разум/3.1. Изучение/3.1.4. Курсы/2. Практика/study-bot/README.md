# Study bot (Модуль 6, Nexara)

Локальный Telegram-бот: сохраняет текст, файлы, фото, транскрибирует голосовые и видео через [Nexara](https://docs.nexara.ru/guides). Исходные учебные материалы **не** меняются: эталон в [`../1. Учебные материалы/Материалы_для_ученика_модуль6/`](../1.%20Учебные%20материалы/Материалы_для_ученика_модуль6/).

## Быстрый старт

```bash
cd study-bot
python3 -m pip install -r requirements.txt
python3 bot.py
```

Первый `/start` в Telegram фиксирует **владельца** в `Распределение/admin.json`; остальные пользователи отклоняются.

## Где работает бот

Нигде «в облаке» по умолчанию: процесс **`python3 bot.py`** на **твоём Mac** из папки `study-bot/`. Пока этот терминал открыт и скрипт не упал — бот в сети. Закрыл терминал или выключил ноутбук — бот молчит.

## Логи (куда смотреть при «зависло на расшифровке»)

1. **Окно терминала**, где запущен `python3 bot.py` — строки с уровнем INFO/ERROR, запросы к Nexara и таймауты.
2. **Файл** [`study-bot/Распределение/bot.log`](Распределение/bot.log) — те же действия в текстовом виде (удобно копировать при разборе).

Таймаут ожидания Nexara задаётся в `TRANSCRIBE_TIMEOUT_SEC` (по умолчанию 180 с) в `config.py` или `.env`.

После клона репозитория, если у тебя нет `config.py`:

```bash
cp config_example.py config.py
# отредактируй ключи TELEGRAM_BOT_TOKEN и NEXARA_API_KEY
```

## Секреты

- `config.py` — рабочий файл с ключами (в `.gitignore`). Шаблон без секретов: `config_example.py`.
- Через окружение (опционально): `TELEGRAM_BOT_TOKEN`, `NEXARA_API_KEY`, `GROQ_*` — см. `.env.example`.

## Groq LLM (текстовый собеседник)

Обычные текстовые сообщения (не ссылка YouTube) отправляются в [Groq](https://console.groq.com) (Chat Completions, бесплатный tier). В `.env`:

- `GROQ_API_KEY` — обязателен (создать в консоли Groq).
- `GROQ_MODEL` — по умолчанию `llama-3.3-70b-versatile`.
- `LLM_SYSTEM_PROMPT` — системный промпт (тон ответов).
- `LLM_TIMEOUT_SEC` — таймаут ответа (сек), по умолчанию 120.

История хранится в `Распределение/llm/history.json`. При большом числе реплик старые **автоматически сжимаются** в выжимку (порог `LLM_SUMMARIZE_THRESHOLD` / `LLM_SUMMARIZE_BATCH` в `.env`) — так сохраняется долгий контекст без переполнения окна модели. Команда `/reset` очищает всё вручную. Ночной сброс по расписанию (`LLM_DAILY_RESET_*`) по умолчанию **выключен**.

## Куда пишутся файлы

Каталог `Распределение/` рядом с проектом:

- `сообщения/` — в текущей версии обычный текст не кладётся сюда (идёт в LLM); папка может быть от старых прогонов
- `транскрипты/` — расшифровки голосовых и видео
- `документы/`, `изображения/` — вложения
- `llm/history.json` — история диалога с Groq LLM (команда `/reset` очищает)

## Безопасность

Если токены попадали в открытый чат — после проверки отзови их: `@BotFather` → `/revoke` для бота; в кабинете Nexara — перевыпусти ключ. Обнови `config.py` или `.env`.

## YouTube-флоу

При получении сообщения с **одной** ссылкой YouTube бот действует так:

1. Сначала пробует снять текст из автогенерируемых/ручных субтитров YouTube через `youtube-transcript-api` — без скачивания и без 403.
2. Если субтитров нет / отключены — скачивает аудио через `yt-dlp` и плагин `bgutil-ytdlp-pot-provider`, **если** в `.env` задан `YT_POT_PROVIDER_URL` и сервис `pot` реально доступен. Иначе бот выставляет `YTDLP_NO_PLUGINS=1` и качает **без** внешних плагинов (часть роликов даст 403 — это ограничение YouTube).

Локально с Docker Desktop:

```bash
./scripts/up_pot.sh
# добавь в .env: YT_POT_PROVIDER_URL=http://127.0.0.1:4416
python3 bot.py
```

Без Docker — субтитры работают; фолбэк yt-dlp — только там, где YouTube пускает без PO-token.

В обоих успешных случаях ответ — `.txt` с полным текстом + копия `.md` в `Распределение/транскрипты/`.

## Деплой на сервере (Docker compose)

В каталоге `study-bot/` лежит [`Dockerfile`](Dockerfile) и [`docker-compose.yml`](docker-compose.yml), поднимают два сервиса:

- `bot` — сам Telegram-бот (polling, без публичного порта).
- `pot` — `brainicism/bgutil-ytdlp-pot-provider`; порт **4416** проброшен на `127.0.0.1` хоста для проверки: `curl -sS http://127.0.0.1:4416/ping`.

Шаги на VPS (Linux + Docker):

```bash
git clone <репозиторий>
cd <репозиторий>/study-bot

cp .env.example .env
# отредактируй TELEGRAM_BOT_TOKEN, NEXARA_API_KEY и GROQ_API_KEY (Groq — для текстового диалога)
# YT_POT_PROVIDER_URL уже указывает на сервис pot внутри сети compose

docker compose up -d --build
docker compose logs -f bot
```

Каталог `Распределение/` пробрасывается volume — транскрипты и `bot.log` сохраняются на хосте.

Чтобы обновиться:

```bash
git pull
docker compose up -d --build
```

Замечания:

- Сервер вне РФ предпочтителен: меньше шансов попасть под бан YouTube/недоступность Telegram.
- `pot`-сервис не требует cookies и обновлений вручную — это и есть «без вмешательства».
- При локальном запуске на ноутбуке без `pot`: бот работает, ссылки YouTube идут через субтитры, а на yt-dlp-фолбэк YouTube может ответить 403.

## Документация API

- [Telegram Bot API](https://core.telegram.org/bots/api) (обзор платформы: [core.telegram.org](https://core.telegram.org/))
- [Nexara — быстрый старт](https://docs.nexara.ru/guides)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- [Groq Console](https://console.groq.com) (API-ключ для LLM)
