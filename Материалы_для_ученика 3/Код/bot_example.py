"""
=============================================================================
TELEGRAM БОТ - ТОЧКА ВХОДА В СИСТЕМУ
=============================================================================
Модуль 6: API Интеграции
Курс: CURSOR - Сотрудники под твой бизнес за вечер

Этот бот:
1. Принимает текст от пользователя
2. Создаёт задачу в Todoist
3. Логирует в Google Sheets
4. Отправляет подтверждение
=============================================================================
"""

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Импортируем конфигурацию
try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID
except ImportError:
    print("ОШИБКА: Создай файл config.py из config_example.py")
    exit(1)

# Импортируем наши интеграции
from todoist_integration import create_task as todoist_create_task
from sheets_integration import log_task as sheets_log_task

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# =============================================================================
# КОМАНДЫ БОТА
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}!\n\n"
        "Я бот для создания задач.\n\n"
        "Команды:\n"
        "/task <текст> — создать задачу\n"
        "/list — показать задачи на сегодня\n"
        "/help — справка\n\n"
        "Или просто напиши текст задачи!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /help"""
    await update.message.reply_text(
        "Как пользоваться ботом:\n\n"
        "1. Напиши текст задачи\n"
        "2. Бот создаст её в Todoist\n"
        "3. Задача залогируется в Google Sheets\n"
        "4. Ты получишь подтверждение\n\n"
        "Пример:\n"
        "/task Позвонить клиенту по поводу договора"
    )


async def create_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /task или текстового сообщения"""

    # Получаем текст задачи
    if context.args:
        # Команда /task с аргументами
        task_text = " ".join(context.args)
    else:
        # Просто текстовое сообщение
        task_text = update.message.text

    if not task_text or task_text.startswith('/'):
        await update.message.reply_text("Напиши текст задачи после команды /task")
        return

    user = update.effective_user

    # Отправляем "печатает..."
    await update.message.chat.send_action("typing")

    try:
        # 1. Создаём задачу в Todoist
        todoist_result = todoist_create_task(task_text)

        # 2. Логируем в Google Sheets
        sheets_result = sheets_log_task(
            task_text=task_text,
            source="Telegram",
            user=user.first_name,
            todoist_id=todoist_result.get("id", "N/A")
        )

        # 3. Отправляем подтверждение
        await update.message.reply_text(
            f"Задача создана!\n\n"
            f"Текст: {task_text}\n"
            f"Todoist ID: {todoist_result.get('id', 'N/A')}\n"
            f"Google Sheets: {'OK' if sheets_result else 'Ошибка'}"
        )

        logger.info(f"Задача создана: {task_text[:50]}...")

    except Exception as e:
        logger.error(f"Ошибка создания задачи: {e}")
        await update.message.reply_text(
            f"Ошибка при создании задачи.\n"
            f"Попробуй ещё раз или проверь настройки."
        )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /list — показать задачи"""
    from todoist_integration import get_tasks

    try:
        tasks = get_tasks()

        if not tasks:
            await update.message.reply_text("Задач на сегодня нет!")
            return

        message = "Задачи на сегодня:\n\n"
        for i, task in enumerate(tasks[:10], 1):  # Максимум 10 задач
            message += f"{i}. {task.get('content', 'Без названия')}\n"

        await update.message.reply_text(message)

    except Exception as e:
        logger.error(f"Ошибка получения задач: {e}")
        await update.message.reply_text("Ошибка при получении задач.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка любого текстового сообщения как задачи"""
    # Передаём в create_task
    context.args = []  # Пустые аргументы, текст возьмём из сообщения
    await create_task(update, context)


# =============================================================================
# ЗАПУСК БОТА
# =============================================================================

def main() -> None:
    """Запуск бота"""

    print("Запуск Telegram бота...")
    print(f"Токен: {TELEGRAM_BOT_TOKEN[:10]}...")

    # Создаём приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("task", create_task))
    application.add_handler(CommandHandler("list", list_tasks))

    # Обработчик текстовых сообщений (любой текст = задача)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен! Нажми Ctrl+C для остановки.")

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
