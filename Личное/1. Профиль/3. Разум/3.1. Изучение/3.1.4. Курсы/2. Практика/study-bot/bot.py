"""
Telegram бот-помощник: сохранение в папку Распределение + транскрибация через Nexara.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path

from telegram import InputFile, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from zoneinfo import ZoneInfo

from config import (
    DISTRIBUTION_FOLDER,
    LLM_DAILY_RESET_ENABLED,
    LLM_DAILY_RESET_HOUR,
    LLM_DAILY_RESET_MINUTE,
    LLM_DAILY_RESET_NOTIFY,
    LLM_DAILY_RESET_TZ,
    MAX_FILE_SIZE,
    TELEGRAM_BOT_TOKEN,
    TRANSCRIBE_TIMEOUT_SEC,
    TRANSCRIPTION_LANGUAGE,
)
import config as _cfg

YOUTUBE_DOWNLOAD_TIMEOUT_SEC = getattr(
    _cfg, "YOUTUBE_DOWNLOAD_TIMEOUT_SEC", 900
)
from chat_history import ChatHistory
from file_saver import FileSaver
from llm_client import chat as groq_chat
from sync_manager import SyncManager
from transcriber import transcribe_voice
from youtube_fetch import (
    download_youtube_best_media,
    extract_youtube_url_for_transcription,
)
from youtube_captions import extract_video_id, fetch_transcript_text

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

file_saver = FileSaver(DISTRIBUTION_FOLDER)
sync_manager = SyncManager(DISTRIBUTION_FOLDER)
chat_history = ChatHistory(DISTRIBUTION_FOLDER)

TG_MESSAGE_MAX_LEN = 4096

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".wav",
    ".flac",
    ".aac",
}


def _transcript_reply_intro(path_md: str) -> str:
    return (
        f"Готово. Копия на диске:\n{path_md}\n\n"
        f"Полный текст — во вложении (.txt)."
    )


async def _safe_reply(message, text: str, *, retries: int = 2) -> bool:
    """
    Отправка статуса с повторами при кратковременных сбоях сети до Telegram.
    """
    for attempt in range(retries + 1):
        try:
            await message.reply_text(text)
            return True
        except (NetworkError, TimedOut) as e:
            logger.warning(
                "reply_text: сеть/Telegram (попытка %s/%s): %s",
                attempt + 1,
                retries + 1,
                e,
            )
            await asyncio.sleep(1.5 * (attempt + 1))
    return False


def _telegram_text_chunks(text: str, max_len: int = TG_MESSAGE_MAX_LEN) -> list[str]:
    """Telegram лимит на одно текстовое сообщение — режем на части."""
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    return [t[i : i + max_len] for i in range(0, len(t), max_len)]


async def _reply_transcript_txt(
    message,
    intro: str,
    transcript: str,
) -> None:
    """Сообщение с путём к .md на диске + полный текст отдельным .txt-файлом."""
    raw = transcript.encode("utf-8")
    if MAX_FILE_SIZE > 0 and len(raw) > MAX_FILE_SIZE:
        await message.reply_text(
            f"{intro}\n\nТранскрипт слишком большой для отправки по "
            f"MAX_FILE_SIZE ({MAX_FILE_SIZE // (1024 * 1024)} МБ). "
            "Полный текст — в .md на диске."
        )
        return
    await message.reply_text(intro.strip())
    fname = f"transcript_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    buf = BytesIO(raw)
    await message.reply_document(document=InputFile(buf, filename=fname))


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


async def _transcribe_nexara(
    message,
    tmp_path: str,
    *,
    stage: str,
) -> str | None:
    """
    Транскрибация с таймаутом. При таймауте пишет пользователю и в лог, возвращает None.
    """
    try:
        sz = Path(tmp_path).stat().st_size
    except OSError:
        sz = 0
    logger.info(
        "Старт Nexara: этап=%s, tmp=%s, размер=%s байт, лимит_сек=%s",
        stage,
        tmp_path,
        sz,
        TRANSCRIBE_TIMEOUT_SEC,
    )
    try:
        text = await asyncio.wait_for(
            transcribe_voice(tmp_path, TRANSCRIPTION_LANGUAGE),
            timeout=TRANSCRIBE_TIMEOUT_SEC,
        )
        return text
    except asyncio.TimeoutError:
        logger.error(
            "Nexara: таймаут %s с (этап=%s)",
            TRANSCRIBE_TIMEOUT_SEC,
            stage,
        )
        sync_manager.log_action(
            "ERROR",
            f"{stage}: таймаут Nexara ({TRANSCRIBE_TIMEOUT_SEC} с)",
        )
        await message.reply_text(
            f"Не дождался ответа Nexara за {TRANSCRIBE_TIMEOUT_SEC} с. "
            "Проверь интернет, ключ API и баланс в кабинете. "
            "Детали — в терминале, где запущен бот, и в Распределение/bot.log."
        )
        return None


async def _try_youtube_captions(
    message,
    user,
    youtube_url: str,
) -> bool:
    """
    Шаг 1: попробовать снять автогенерируемые/ручные субтитры YouTube.
    Возвращает True, если транскрипт отдан пользователю.
    """
    video_id = extract_video_id(youtube_url)
    if not video_id:
        return False
    await _safe_reply(message, "Беру субтитры YouTube...")
    try:
        text = await asyncio.to_thread(fetch_transcript_text, video_id)
    except Exception as e:
        logger.warning("YouTube subs ошибка: %s", e)
        return False
    if not text:
        return False
    path_md = file_saver.save_transcript(text, "youtube_captions")
    sync_manager.log_action(
        "YOUTUBE",
        f"Субтитры YouTube от @{user.username or user.id}: {youtube_url[:96]}",
    )
    sync_manager.mark_as_processed(message.message_id)
    await _reply_transcript_txt(
        message,
        _transcript_reply_intro(path_md)
        + "\n\nИсточник: субтитры YouTube (без скачивания).",
        text,
    )
    return True


async def _youtube_transcribe_flow(
    message,
    user,
    youtube_url: str,
) -> None:
    tmp_media: str | None = None
    workdir: str | None = None
    try:
        if await _try_youtube_captions(message, user, youtube_url):
            return
        ok = await _safe_reply(
            message,
            "Субтитров нет, скачиваю аудио и отправляю в Nexara "
            "(может занять несколько минут)...",
        )
        if not ok:
            logger.error("YouTube: не удалось отправить статус в Telegram (сеть)")
        try:
            tmp_media, workdir = await asyncio.wait_for(
                asyncio.to_thread(download_youtube_best_media, youtube_url),
                timeout=YOUTUBE_DOWNLOAD_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            sync_manager.log_action("ERROR", "YouTube: таймаут скачивания")
            await _safe_reply(
                message,
                f"Скачивание с YouTube не уложилось в {YOUTUBE_DOWNLOAD_TIMEOUT_SEC} с. "
                "Попробуй другой ролик или проверь сеть.",
            )
            return
        except Exception as e:
            logger.exception("yt-dlp: ошибка скачивания")
            sync_manager.log_action("ERROR", f"yt-dlp: {e}")
            err_s = str(e)
            if "DRM" in err_s or "drm" in err_s.lower():
                hint = (
                    "Видео защищено DRM (YouTube Premium / лицензионный контент) — "
                    "скачать его невозможно технически. Попробуй другой ролик."
                )
            elif "403" in err_s:
                hint = (
                    "YouTube вернул 403 — часто нет рабочего PO-token сервиса.\n"
                    "— На сервере: контейнер `study-bot-pot` (Up), проверка: "
                    "`curl -sS http://127.0.0.1:4416/ping`.\n"
                    "— Локально без Docker: в `.env` должен быть "
                    "`YT_POT_PROVIDER_URL=http://127.0.0.1:4416` и команда "
                    "`docker compose up -d pot`; либо удали пакет "
                    "`bgutil-ytdlp-pot-provider` и перезапусти бота."
                )
            else:
                hint = (
                    "Проверь ссылку; установлены yt-dlp и ffmpeg; на сервере — "
                    "контейнер `pot` из docker-compose.yml."
                )
            await _safe_reply(
                message,
                f"Не получилось скачать ролик: {e}\n\n{hint}",
            )
            return
        try:
            sz = Path(tmp_media).stat().st_size
        except OSError:
            sz = 0
        if MAX_FILE_SIZE > 0 and sz > MAX_FILE_SIZE:
            await _safe_reply(
                message,
                f"После скачивания файл ~{sz // (1024 * 1024)} МБ — превышает MAX_FILE_SIZE "
                f"({MAX_FILE_SIZE // (1024 * 1024)} МБ). В .env задай MAX_FILE_SIZE=0 "
                "или большее значение.",
            )
            sync_manager.log_action("ERROR", f"YouTube: размер {sz} > лимит")
            return
        await _safe_reply(
            message,
            f"Скачано (~{max(1, sz // 1024)} КБ). Запрос к Nexara "
            f"(до {TRANSCRIBE_TIMEOUT_SEC} с)...",
        )
        transcript = await _transcribe_nexara(
            message, tmp_media, stage="youtube"
        )
        if transcript is None:
            return
        path_md = file_saver.save_transcript(transcript, "youtube")
        sync_manager.log_action(
            "YOUTUBE",
            f"YouTube от @{user.username or user.id}: {youtube_url[:96]}",
        )
        sync_manager.mark_as_processed(message.message_id)
        await _reply_transcript_txt(
            message,
            _transcript_reply_intro(path_md),
            transcript,
        )
    except Exception as e:
        logger.exception("Ошибка транскрибации YouTube")
        sync_manager.log_action("ERROR", f"YouTube: {e}")
        await _safe_reply(
            message,
            f"Не удалось обработать ссылку YouTube: {e}\n\n"
            "Проверь интернет и доступ к Telegram (api.telegram.org).",
        )
    finally:
        if tmp_media:
            Path(tmp_media).unlink(missing_ok=True)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


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
            "• Диалог по тексту с Groq LLM (нужен GROQ_API_KEY в .env)\n"
            "• Транскрибировать голосовые, аудио, видео и ссылку YouTube (Nexara)\n"
            "• Сохранять документы и изображения\n\n"
            "Команды: /help /status /sync /log /list /reset\n\n"
            "Файлы и история LLM — в папку Распределение рядом с ботом."
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
        "Снова привет! Обычный текст — диалог с LLM (Groq). Ссылка на YouTube (одной строкой), голосовое, файл или фото — как раньше.\n\n"
        "/help — справка\n/reset — очистить историю с LLM"
    )
    await update.message.reply_text(text)


@require_admin
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_message = """
📋 *Справка*

*Контент:*
• Текст (не YouTube) → **Groq LLM**; длинная история с **авто-саммаризацией** (`history.json`). `/reset` — очистить; ночной сброс по умолчанию выключен.
• Голосовые, аудио-файлы и видео → транскрипт в `Распределение/транскрипты/` + ответ файлом `.txt` (Nexara)
• Одна строка только с ссылкой на YouTube (watch, Shorts, youtu.be) → скачивание и тот же сценарий (нужны `yt-dlp` и по ситуации `ffmpeg`)
• Документы → `Распределение/документы/`
• Фото → `Распределение/изображения/`

*Команды:*
/start — старт
/help — эта справка
/status — путь к папке
/sync — статистика сессии
/log — последние записи лога
/list — последние 10 сохранённых файлов
/reset — очистить историю с LLM вручную (выжимки и реплики). По расписанию ночью — только если включён `LLM_DAILY_RESET_ENABLED`

Нужен `GROQ_API_KEY` в `.env` (console.groq.com). Бот может работать на сервере (Docker).
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
async def list_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    files = file_saver.get_recent_files(10)
    if not files:
        await update.message.reply_text("Файлов пока нет.")
        return
    lines = [f"• [{f['folder']}] {f['name']}" for f in files]
    await update.message.reply_text(
        "Последние 10 файлов:\n" + "\n".join(lines)
    )


@require_admin
async def reset_history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_history.reset()
    sync_manager.log_action("LLM", "История диалога очищена (/reset)")
    await update.message.reply_text("История диалога с LLM очищена.")


@require_admin
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if sync_manager.is_processed(message.message_id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    yt_url = extract_youtube_url_for_transcription(text)
    if yt_url:
        await _youtube_transcribe_flow(message, user, yt_url)
        return

    username = user.username or "unknown"
    try:
        try:
            if await chat_history.summarize_if_needed():
                sync_manager.log_action("LLM", "Авто-саммаризация истории")
        except Exception:
            logger.exception("chat_history.summarize_if_needed")
            sync_manager.log_action("ERROR", "LLM: ошибка саммаризации истории")
        hist = chat_history.recent(20)
        rolling = chat_history.rolling_summary_contents()
        reply_text = await groq_chat(hist, text, rolling_summaries=rolling)
    except Exception as e:
        logger.exception("Groq LLM")
        sync_manager.log_action("ERROR", f"LLM: {e}")
        await message.reply_text(
            f"Не удалось получить ответ от LLM: {e}\n\n"
            "Проверь GROQ_API_KEY в .env (console.groq.com)."
        )
        return

    chat_history.append("user", text)
    chat_history.append("assistant", reply_text)
    sync_manager.log_action("LLM", f"Groq от @{username}, len={len(reply_text)}")
    sync_manager.mark_as_processed(message.message_id)
    for chunk in _telegram_text_chunks(reply_text):
        await message.reply_text(chunk)


@require_admin
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    voice = message.voice
    if voice is None:
        return
    if sync_manager.is_processed(message.message_id):
        return
    await _safe_reply(message, "Получил голосовое, транскрибирую...")
    suffix = ".ogg"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as tmp:
            tmp_path = tmp.name
        vf = await voice.get_file()
        await vf.download_to_drive(tmp_path)
        try:
            kb = Path(tmp_path).stat().st_size // 1024
        except OSError:
            kb = 0
        await _safe_reply(
            message,
            f"Файл скачан (~{kb} КБ), запрос к Nexara "
            f"(не дольше {TRANSCRIBE_TIMEOUT_SEC} с)...",
        )
        transcript = await _transcribe_nexara(
            message, tmp_path, stage="голос"
        )
        if transcript is None:
            return
        path_md = file_saver.save_transcript(transcript, "voice")
        sync_manager.log_action(
            "VOICE", f"Транскрипт голосового от @{user.username or user.id}"
        )
        sync_manager.mark_as_processed(message.message_id)
        await _reply_transcript_txt(
            message,
            _transcript_reply_intro(path_md),
            transcript,
        )
    except Exception as e:
        logger.exception("Ошибка транскрибации голоса")
        sync_manager.log_action("ERROR", f"Голос: {e}")
        await message.reply_text(f"Ошибка транскрибации: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@require_admin
async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    audio = message.audio
    if audio is None:
        return
    if sync_manager.is_processed(message.message_id):
        return
    if (
        audio.file_size
        and MAX_FILE_SIZE > 0
        and audio.file_size > MAX_FILE_SIZE
    ):
        await message.reply_text("Аудиофайл слишком большой.")
        return

    await _safe_reply(message, "Получил аудио, транскрибирую...")
    ext = Path(audio.file_name or "").suffix.lower() or ".mp3"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
        af = await audio.get_file()
        await af.download_to_drive(tmp_path)
        try:
            kb = Path(tmp_path).stat().st_size // 1024
        except OSError:
            kb = 0
        await _safe_reply(
            message,
            f"Файл скачан (~{kb} КБ), запрос к Nexara "
            f"(до {TRANSCRIBE_TIMEOUT_SEC} с)...",
        )
        transcript = await _transcribe_nexara(
            message, tmp_path, stage="аудио"
        )
        if transcript is None:
            return
        path_md = file_saver.save_transcript(transcript, "audio")
        sync_manager.log_action(
            "AUDIO",
            f"Транскрипт аудио от @{user.username or user.id}",
        )
        sync_manager.mark_as_processed(message.message_id)
        await _reply_transcript_txt(
            message,
            _transcript_reply_intro(path_md),
            transcript,
        )
    except Exception as e:
        logger.exception("Ошибка транскрибации аудио")
        sync_manager.log_action("ERROR", f"Аудио: {e}")
        await message.reply_text(f"Ошибка транскрибации: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _doc_size_ok(doc) -> bool:
    if doc.file_size is None:
        return True
    if MAX_FILE_SIZE <= 0:
        return True
    return doc.file_size <= MAX_FILE_SIZE


def _is_audio_doc(doc, ext: str) -> bool:
    if doc.mime_type and str(doc.mime_type).startswith("audio/"):
        return True
    return ext in AUDIO_EXTENSIONS


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

    if sync_manager.is_processed(message.message_id):
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
            await _safe_reply(
                message, "Получил видео-файл, транскрибирую..."
            )
            transcript = await _transcribe_nexara(
                message, tmp_path, stage=f"видео {name}"
            )
            if transcript is None:
                return
            path_md = file_saver.save_transcript(transcript, "video")
            sync_manager.log_action(
                "VIDEO", f"Транскрипт видео {name}"
            )
            await _reply_transcript_txt(
                message,
                _transcript_reply_intro(path_md),
                transcript,
            )
        elif _is_audio_doc(doc, ext):
            await _safe_reply(
                message, "Получил аудио-файл, транскрибирую..."
            )
            transcript = await _transcribe_nexara(
                message, tmp_path, stage=f"аудио {name}"
            )
            if transcript is None:
                return
            path_md = file_saver.save_transcript(transcript, "audio")
            sync_manager.log_action(
                "AUDIO", f"Транскрипт аудио (документ) {name}"
            )
            await _reply_transcript_txt(
                message,
                _transcript_reply_intro(path_md),
                transcript,
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
    if (
        photo.file_size
        and MAX_FILE_SIZE > 0
        and photo.file_size > MAX_FILE_SIZE
    ):
        await message.reply_text("Фото слишком большое.")
        return

    if sync_manager.is_processed(message.message_id):
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
    if (
        vid.file_size
        and MAX_FILE_SIZE > 0
        and vid.file_size > MAX_FILE_SIZE
    ):
        await message.reply_text("Видео слишком большое.")
        return

    if sync_manager.is_processed(message.message_id):
        return

    await _safe_reply(message, "Получил видео, транскрибирую...")
    tmp_path = None
    try:
        ext = Path(vid.file_name or "").suffix.lower() or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
        vf = await vid.get_file()
        await vf.download_to_drive(tmp_path)
        transcript = await _transcribe_nexara(
            message, tmp_path, stage="video"
        )
        if transcript is None:
            return
        path_md = file_saver.save_transcript(transcript, "video")
        sync_manager.log_action("VIDEO", "Транскрипт video-сообщения")
        sync_manager.mark_as_processed(message.message_id)
        await _reply_transcript_txt(
            message,
            _transcript_reply_intro(path_md),
            transcript,
        )
    except Exception as e:
        logger.exception("Ошибка видео")
        sync_manager.log_action("ERROR", f"Видео: {e}")
        await message.reply_text(f"Ошибка: {e}")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


async def _on_started(application: Application) -> None:
    admin_id = sync_manager.get_admin_id()
    if admin_id is None:
        return
    try:
        await application.bot.send_message(
            chat_id=admin_id,
            text="Бот запущен и готов. /sync — сводка сессии.",
        )
    except Exception:
        logger.exception("Не удалось отправить отчёт о старте админу")


async def _llm_history_daily_reset_loop(application: Application) -> None:
    """Раз в сутки в заданное локальное время очищает LLM history.json."""
    try:
        tz = ZoneInfo(LLM_DAILY_RESET_TZ)
    except Exception:
        logger.warning(
            "LLM_DAILY_RESET_TZ=%r недоступен, используем UTC",
            LLM_DAILY_RESET_TZ,
        )
        tz = ZoneInfo("UTC")
    hour = max(0, min(23, LLM_DAILY_RESET_HOUR))
    minute = max(0, min(59, LLM_DAILY_RESET_MINUTE))
    while True:
        now = datetime.now(tz)
        target = now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        logger.info(
            "LLM daily reset: следующий сброс %s (через %.0f с)",
            target.isoformat(),
            delay,
        )
        await asyncio.sleep(delay)
        chat_history.reset()
        sync_manager.log_action(
            "LLM", "Автосброс истории LLM (ночное расписание)"
        )
        if LLM_DAILY_RESET_NOTIFY:
            aid = sync_manager.get_admin_id()
            if aid is not None:
                try:
                    await application.bot.send_message(
                        chat_id=aid,
                        text="История диалога с LLM обнулена (ночной сброс).",
                    )
                except Exception:
                    logger.exception(
                        "Не удалось уведомить админа о ночном сбросе LLM"
                    )


async def _post_init(application: Application) -> None:
    if LLM_DAILY_RESET_ENABLED:
        asyncio.create_task(
            _llm_history_daily_reset_loop(application)
        )
    await _on_started(application)


def main():
    print("Запуск бота...")
    sync_manager.log_action("STARTUP", "Бот запущен")
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(20.0)
        .read_timeout(40.0)
        .write_timeout(40.0)
        .pool_timeout(10.0)
        .get_updates_connect_timeout(20.0)
        .get_updates_read_timeout(40.0)
        .post_init(_post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("log", log_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("reset", reset_history_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_handler(MessageHandler(filters.AUDIO, audio_handler))
    application.add_handler(
        MessageHandler(filters.Document.ALL, document_handler)
    )
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.VIDEO, video_handler))

    print("Бот запущен. Ctrl+C — остановка.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
