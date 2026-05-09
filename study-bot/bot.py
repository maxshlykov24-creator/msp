"""
Telegram бот-помощник: сохранение в папку Распределение + транскрибация через Nexara.
"""

from __future__ import annotations

import logging
import tempfile
from functools import wraps
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    DISTRIBUTION_FOLDER,
    MAX_FILE_SIZE,
    TELEGRAM_BOT_TOKEN,
    TRANSCRIPTION_LANGUAGE,
)
from file_saver import FileSaver
from sync_manager import SyncManager
from transcriber import transcribe_video, transcribe_voice

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

file_saver = FileSaver(DISTRIBUTION_FOLDER)
sync_manager = SyncManager(DISTRIBUTION_FOLDER)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


def require_admin(handler):
    @wraps(handler)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if update.effective_user is None or update.message is None:
            return
        admin_id = sync_manager.get_admin_id()
        if admin_id is None:
            await update.message.reply_text(
                "Сначала владелец должен отправить /start — так бот "
                "запомнит твой Telegram ID."
            )
            return
        if update.effective_user.id != admin_id:
            logger.info(
                "Игнор: user_id=%s (!= admin)",
                update.effective_user.id,
            )
            await update.message.reply_text("Этот бот только для владельца.")
            return
        return await handler(update, context)

    return wrapped


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return
    uid = update.effective_user.id
    if sync_manager.register_admin_if_absent(uid):
        sync_manager.log_action("START", f"Пользователь {uid} зарегистрирован как владелец")
        text = (
            "Привет! Я бот-помощник для обучения.\n\n"
            "Ты зарегистрирован как владелец этого бота — другие "
            "пользователи не смогут им пользоваться.\n\n"
            "Я умею:\n"
            "• Сохранять текст\n"
            "• Транскрибировать голосовые и видео (Nexara)\n"
            "• Сохранять документы и изображения\n\n"
            "Команды: /help /status /sync /log\n\n"
            "Файлы сохраняются в папку Распределение рядом с ботом."
        )
        await update.message.reply_text(text)
        return

    if uid != sync_manager.get_admin_id():
        await update.message.reply_text(
            "Этот бот только для владельца. Доступ запрещён."
        )
        return

    sync_manager.log_action("START", f"Владелец {uid} открыл бота")
    text = (
        "Снова привет! Отправь текст, голосовое, файл или фото — "
        "всё сложится в папку Распределение.\n\n"
        "/help — справка"
    )
    await update.message.reply_text(text)


@require_admin
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_message = """
📋 *Справка*

*Контент:*
• Текст → `Распределение/сообщения/`
• Голосовые и видео → транскрипт в `Распределение/транскрипты/` (Nexara)
• Документы → `Распределение/документы/`
• Фото → `Распределение/изображения/`

*Команды:*
/start — старт
/help — эта справка
/status — путь к папке
/sync — статистика сессии
/log — последние записи лога

Бот работает, пока запущен на твоём компьютере.
"""
    await update.message.reply_text(help_message, parse_mode="Markdown")


@require_admin
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aid = sync_manager.get_admin_id()
    status_message = f"""📊 *Статус*

✅ Бот работает
📁 Папка: `{DISTRIBUTION_FOLDER}`
👤 Владелец (Telegram ID): `{aid}`

Отправь сообщение для проверки.
"""
    await update.message.reply_text(status_message, parse_mode="Markdown")


@require_admin
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = sync_manager.format_stats_message()
    await update.message.reply_text(msg, parse_mode="Markdown")


@require_admin
async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = sync_manager.format_logs_message(10)
    await update.message.reply_text(msg, parse_mode="Markdown")


@require_admin
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    text = message.text
    username = user.username or "unknown"
    user_id = user.id
    filepath = file_saver.save_message(text, username, user_id)
    sync_manager.log_action("TEXT", f"Сохранено от @{username}")
    sync_manager.mark_as_processed(message.message_id)
    await message.reply_text(f"Сообщение сохранено:\n{filepath}")


@require_admin
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    await message.reply_text("Получил голосовое, транскрибирую...")
    voice = message.voice
    suffix = ".ogg"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as tmp:
            tmp_path = tmp.name
        vf = await voice.get_file()
        await vf.download_to_drive(tmp_path)
        transcript = await transcribe_voice(
            tmp_path, TRANSCRIPTION_LANGUAGE
        )
        path_md = file_saver.save_transcript(transcript, "voice")
        sync_manager.log_action(
            "VOICE", f"Транскрипт голосового от @{user.username or user.id}"
        )
        sync_manager.mark_as_processed(message.message_id)
        snippet = transcript[:3500] + (
            "…" if len(transcript) > 3500 else ""
        )
        await message.reply_text(
            f"Готово. Файл:\n{path_md}\n\n---\n{snippet}"
        )
    except Exception as e:
        logger.exception("Ошибка транскрибации голоса")
        sync_manager.log_action("ERROR", f"Голос: {e}")
        await message.reply_text(f"Ошибка транскрибации: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _doc_size_ok(doc) -> bool:
    if doc.file_size is None:
        return True
    return doc.file_size <= MAX_FILE_SIZE


@require_admin
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    doc = message.document
    if doc is None:
        return
    if not _doc_size_ok(doc):
        await message.reply_text(
            f"Файл слишком большой (лимит {MAX_FILE_SIZE // (1024 * 1024)} МБ)."
        )
        return

    name = doc.file_name or "file"
    ext = Path(name).suffix.lower()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext or "") as tmp:
            tmp_path = tmp.name
        df = await doc.get_file()
        await df.download_to_drive(tmp_path)

        if ext in VIDEO_EXTENSIONS:
            await message.reply_text("Получил видео-файл, транскрибирую...")
            transcript = await transcribe_video(
                tmp_path, TRANSCRIPTION_LANGUAGE
            )
            path_md = file_saver.save_transcript(transcript, "video")
            sync_manager.log_action(
                "VIDEO", f"Транскрипт видео {name}"
            )
            snippet = transcript[:3500] + (
                "…" if len(transcript) > 3500 else ""
            )
            await message.reply_text(
                f"Готово. Транскрипт:\n{path_md}\n\n---\n{snippet}"
            )
        else:
            dest = file_saver.save_document(tmp_path, name)
            sync_manager.log_action("DOCUMENT", f"Сохранён {name}")
            await message.reply_text(f"Документ сохранён:\n{dest}")

        sync_manager.mark_as_processed(message.message_id)
    except Exception as e:
        logger.exception("Ошибка обработки документа")
        sync_manager.log_action("ERROR", f"Документ: {e}")
        await message.reply_text(f"Ошибка: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@require_admin
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    photos = message.photo
    if not photos:
        return
    photo = photos[-1]
    if photo.file_size and photo.file_size > MAX_FILE_SIZE:
        await message.reply_text("Фото слишком большое.")
        return

    tmp_path = None
    try:
        pf = await photo.get_file()
        ext = Path(pf.file_path or ".jpg").suffix or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
        await pf.download_to_drive(tmp_path)
        dest = file_saver.save_image(tmp_path)
        sync_manager.log_action("IMAGE", f"Фото сохранено")
        sync_manager.mark_as_processed(message.message_id)
        await message.reply_text(f"Изображение сохранено:\n{dest}")
    except Exception as e:
        logger.exception("Ошибка сохранения фото")
        sync_manager.log_action("ERROR", f"Фото: {e}")
        await message.reply_text(f"Ошибка: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@require_admin
async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    vid = message.video
    if vid is None:
        return
    if vid.file_size and vid.file_size > MAX_FILE_SIZE:
        await message.reply_text("Видео слишком большое.")
        return

    await message.reply_text("Получил видео, транскрибирую...")
    tmp_path = None
    try:
        ext = Path(vid.file_name or "").suffix.lower() or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
        vf = await vid.get_file()
        await vf.download_to_drive(tmp_path)
        transcript = await transcribe_video(tmp_path, TRANSCRIPTION_LANGUAGE)
        path_md = file_saver.save_transcript(transcript, "video")
        sync_manager.log_action("VIDEO", "Транскрипт video-сообщения")
        sync_manager.mark_as_processed(message.message_id)
        snippet = transcript[:3500] + (
            "…" if len(transcript) > 3500 else ""
        )
        await message.reply_text(
            f"Готово. Транскрипт:\n{path_md}\n\n---\n{snippet}"
        )
    except Exception as e:
        logger.exception("Ошибка видео")
        sync_manager.log_action("ERROR", f"Видео: {e}")
        await message.reply_text(f"Ошибка: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def main():
    print("Запуск бота...")
    sync_manager.log_action("STARTUP", "Бот запущен")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("log", log_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_handler(
        MessageHandler(filters.Document.ALL, document_handler)
    )
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.VIDEO, video_handler))

    print("Бот запущен. Ctrl+C — остановка.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
