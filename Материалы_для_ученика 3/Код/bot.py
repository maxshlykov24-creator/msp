"""
Telegram бот-помощник для обучения
Курс: CURSOR — Сотрудники под твой бизнес за вечер
Модуль 6: API Интеграции

Это шаблон бота. 70% кода готово, 30% нужно дописать по комментариям TODO.
"""

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Импортируем наши модули
from config import TELEGRAM_BOT_TOKEN, DISTRIBUTION_FOLDER, ADMIN_ID
from file_saver import FileSaver
from transcriber import transcribe_voice, transcribe_video
from sync_manager import SyncManager

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализируем модули
file_saver = FileSaver(DISTRIBUTION_FOLDER)
sync_manager = SyncManager(DISTRIBUTION_FOLDER)


# ============================================================================
# КОМАНДЫ БОТА (ГОТОВО)
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = """
🤖 *Привет! Я твой бот-помощник для обучения.*

Я умею:
• Сохранять текстовые сообщения
• Транскрибировать голосовые
• Сохранять документы и изображения
• Вести логи работы

Просто отправь мне что-нибудь, и я сохраню это в папку Распределение.

📋 *Команды:*
/help - справка
/status - статус бота
/sync - статистика синхронизации
/log - последние записи логов
"""
    await update.message.reply_text(welcome_message, parse_mode="Markdown")
    sync_manager.log_action("START", f"Пользователь {update.effective_user.id} запустил бота")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_message = """
📋 *Справка по боту*

*Что я умею:*
• Текст → сохраняю в Распределение/сообщения/
• Голосовые → транскрибирую через AssemblyAI
• Документы → сохраняю в Распределение/документы/
• Изображения → сохраняю в Распределение/изображения/

*Команды:*
/start - приветствие
/help - эта справка
/status - показать путь к папке
/sync - статистика синхронизации
/log - последние 10 записей логов

*Важно:*
Бот работает только когда твой компьютер включен.
"""
    await update.message.reply_text(help_message, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    status_message = f"""
📊 *Статус бота*

✅ Бот работает
📁 Папка: `{DISTRIBUTION_FOLDER}`
👤 Админ ID: `{ADMIN_ID}`

Отправь любое сообщение для проверки.
"""
    await update.message.reply_text(status_message, parse_mode="Markdown")


# ============================================================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# ============================================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (ГОТОВО)"""
    message = update.message
    user = update.effective_user
    
    # Получаем данные
    text = message.text
    username = user.username or "unknown"
    user_id = user.id
    
    # Сохраняем сообщение
    filepath = file_saver.save_message(text, username, user_id)
    
    # Логируем
    sync_manager.log_action("TEXT", f"Сохранено сообщение от {username}")
    sync_manager.mark_as_processed(message.message_id)
    
    # Отвечаем
    await message.reply_text(f"✅ Сообщение сохранено:\n`{filepath}`", parse_mode="Markdown")


# TODO 2: Добавить обработчик голосовых сообщений
# ============================================================================
# ПОДСКАЗКА:
# 1. Получи файл голосового: voice = await message.voice.get_file()
# 2. Скачай файл: await voice.download_to_drive(temp_path)
# 3. Транскрибируй: transcript = await transcribe_voice(temp_path)
# 4. Сохрани транскрипт: file_saver.save_transcript(transcript, "voice")
# 5. Ответь пользователю с транскриптом
# ============================================================================
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений"""
    message = update.message
    user = update.effective_user
    
    await message.reply_text("🎤 Получил голосовое, транскрибирую...")
    
    # TODO 2: Допиши код транскрибации голосового сообщения
    # Используй подсказку выше
    # 
    # Твой код здесь:
    # ...
    
    pass  # Удали эту строку когда допишешь код


# TODO 3: Добавить обработчик документов
# ============================================================================
# ПОДСКАЗКА:
# 1. Получи файл: document = await message.document.get_file()
# 2. Получи имя файла: filename = message.document.file_name
# 3. Скачай файл: await document.download_to_drive(путь)
# 4. Используй file_saver.save_document(путь, filename)
# 5. Ответь пользователю
# ============================================================================
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов"""
    message = update.message
    user = update.effective_user
    
    # TODO 3: Допиши код сохранения документа
    # Используй подсказку выше
    #
    # Твой код здесь:
    # ...
    
    pass  # Удали эту строку когда допишешь код


# TODO 3.5: Добавить обработчик изображений (по аналогии с документами)
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик изображений"""
    message = update.message
    user = update.effective_user
    
    # ПОДСКАЗКА:
    # 1. Получи фото (берём самое большое): photo = message.photo[-1]
    # 2. Получи файл: file = await photo.get_file()
    # 3. Сохрани: file_saver.save_image(путь)
    
    # Твой код здесь:
    # ...
    
    pass  # Удали эту строку когда допишешь код


# TODO 4: Добавить команды /sync и /log
# ============================================================================
# ПОДСКАЗКА для /sync:
# 1. Вызови sync_manager.get_sync_stats()
# 2. Сформируй красивое сообщение
# 3. Отправь пользователю
#
# ПОДСКАЗКА для /log:
# 1. Вызови sync_manager.get_last_logs(10)
# 2. Сформируй сообщение из списка логов
# 3. Отправь пользователю
# ============================================================================
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /sync"""
    # TODO 4: Допиши код команды /sync
    # Используй подсказку выше
    #
    # Твой код здесь:
    # ...
    
    await update.message.reply_text("📊 Команда /sync пока не реализована. Допиши TODO 4!")


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /log"""
    # TODO 4: Допиши код команды /log
    # Используй подсказку выше
    #
    # Твой код здесь:
    # ...
    
    await update.message.reply_text("📋 Команда /log пока не реализована. Допиши TODO 4!")


# ============================================================================
# MAIN - ЗАПУСК БОТА (ГОТОВО)
# ============================================================================

def main():
    """Главная функция запуска бота"""
    
    # Логируем старт
    print("🚀 Запуск бота...")
    sync_manager.log_action("STARTUP", "Бот запущен")
    
    # Создаём приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("log", log_command))
    
    # Регистрируем обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    
    # Запускаем бота
    print("✅ Бот запущен! Нажми Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

