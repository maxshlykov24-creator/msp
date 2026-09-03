# Бонус · Свой TG-бот на VPS из М3

> Когда базовый Cursor-скрипт зашёл и пользуешься каждый день — переходи на бот. Он висит 24/7 на твоей VPS из М3, ты кидаешь ему файл прямо из Telegram (с телефона, на ходу, после встречи) — он отвечает `.md`-файлом. Без открытия ноута.

⚠️ **Это бонус, не базовая часть М4.** Сначала собери базовый трек (Cursor + скрипт), пользуйся 1–2 недели, пойми что тебе реально нужно — потом обернёшь в бот.

## Зачем

- **Транскрибация на ходу:** после встречи закинул голосовое — через минуту в чате готовый текст.
- **Не нужен открытый ноут:** телефон + Telegram = достаточно.
- **Бот в систему:** ученики / ассистенты / партнёры могут кидать ему тоже (по белому списку TG-ID).
- **Постоянная привычка:** когда инструмент в кармане — фиксируешь больше.

## Что понадобится

- ✅ VPS из М3 (уже есть, на ней крутится OpenClaw).
- ✅ Те же 1–3 ключа STT (AssemblyAI / OpenRouter / MyMeet) — те что уже в `.env`.
- ✅ Новый TG-бот через @BotFather (отдельный от М3 — там был sales-бот).
- ✅ ~30 минут на установку.

## Как это сделано

Ровно тот же `transcribe.py` обёрнут в [aiogram](https://docs.aiogram.dev/)-хендлер. Логика:

```
@dp.message(F.voice | F.audio | F.video | F.document)
async def on_media(msg: Message):
    file_path = await download_telegram_file(msg)
    result = transcribe_assemblyai(file_path)  # или auto
    md_path = save_markdown(result, file_path, detect_category(...))
    await msg.reply_document(FSInputFile(md_path))
```

Никакого нового кода — переиспользуем функции из `transcribe.py`.

## 4 шага деплоя

```
[1] Создать бота у @BotFather       (2 мин)
[2] scp папки StartPack_M4/ на VPS  (1 мин)
[3] pip install aiogram + создать   (5 мин)
    bot.py из шаблона ниже
[4] systemd-юнит + запуск           (3 мин)
```

## Шаблон `bot.py` (поверх transcribe.py)

```python
import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message

from transcribe import (
    auto_provider, transcribe_assemblyai, transcribe_openrouter,
    detect_category, save_markdown,
)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(msg: Message):
    await msg.answer(
        "🎙 Кидай мне любое аудио/видео/голосовое — отвечу .md-файлом "
        "со спикерами и тайм-кодами.\n\n"
        "Подсказки в caption: #встреча → AssemblyAI с диаризацией; "
        "#голосовое → Whisper дёшево; #клиент / #подрядчик / #эксперт "
        "→ соответствующая папка."
    )


@dp.message(F.voice | F.audio | F.video | F.document)
async def on_media(msg: Message):
    if ALLOWED_USERS and msg.from_user.id not in ALLOWED_USERS:
        return await msg.reply("Доступ только владельцу. Пиши @denispleada.")

    await msg.reply("Принял. Транскрибирую…")

    # Скачиваем файл с серверов Telegram (лимит TG Bot API — 20 МБ)
    file = msg.voice or msg.audio or msg.video or msg.document
    tg_file = await bot.get_file(file.file_id)
    local = Path("/tmp") / f"m4_{file.file_id}_{file.file_unique_id}"
    await bot.download_file(tg_file.file_path, destination=local)

    hint = msg.caption or ""
    provider = auto_provider(local, hint)
    fn = {"assembly": transcribe_assemblyai, "openrouter": transcribe_openrouter}[provider]

    try:
        result = fn(local)
    except Exception as e:
        return await msg.reply(f"Ошибка: {e}")

    category = detect_category(local.name, hint)
    md_path = save_markdown(result, local, category)
    await msg.reply_document(FSInputFile(md_path), caption=f"Готово. Папка: {category}.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
```

## systemd-юнит

`/etc/systemd/system/m4-transcriber-bot.service`:

```ini
[Unit]
Description=M4 Transcriber TG bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/m4-transcriber
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now m4-transcriber-bot.service
journalctl -u m4-transcriber-bot.service -f  # смотрим логи
```

## Лимит файла из TG Bot API

19 МБ. Длинная Zoom-запись (1 час mp4) не пролезет. В таком случае:
1. Залей файл в Google Drive с публичной ссылкой.
2. Кинь боту **текст со ссылкой**.
3. Бот распознает ссылку и скачает напрямую (минуя TG).

Логика обработки ссылок — расширение `on_message` хендлера. Не входит в базовый шаблон, добавляется по мере роста.

## Дальше · мост в М5

Тот же бот + тот же скрипт + та же VPS — это **основа М5 (Слушатель звонков ОКК).** В М5 добавится:
- LLM-аудит каждого звонка по чек-листу (твоему промпту качества разговора)
- Google Sheets как дашборд по менеджерам
- Webhook от CRM (звонок завершился → бот забрал запись → оценил → положил в Sheets)

Если ты уже собрал бот в М4 — М5 это всего 2–3 дополнительные ноды поверх.
