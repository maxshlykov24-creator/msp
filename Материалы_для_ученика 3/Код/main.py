"""
=============================================================================
ГЛАВНЫЙ СКРИПТ - ОБЪЕДИНЁННАЯ СИСТЕМА
=============================================================================
Модуль 6: API Интеграции
Курс: CURSOR - Сотрудники под твой бизнес за вечер

Этот скрипт объединяет все интеграции:
1. Telegram бот (точка входа)
2. Todoist API (таск-менеджер)
3. Google Sheets (логирование)

Архитектура:
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Telegram   │ ──▶ │   Python    │ ──▶ │   Todoist   │
│    Бот      │     │   Скрипт    │     │     API     │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │   Google    │
                   │   Sheets    │
                   └─────────────┘
=============================================================================
"""

import logging
import sys
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def check_config() -> bool:
    """Проверяет наличие конфигурации"""
    try:
        from config import (
            TELEGRAM_BOT_TOKEN,
            TODOIST_API_TOKEN,
            GOOGLE_SPREADSHEET_ID
        )

        missing = []

        if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
            missing.append("TELEGRAM_BOT_TOKEN")

        if not TODOIST_API_TOKEN or TODOIST_API_TOKEN == "YOUR_TODOIST_API_TOKEN":
            missing.append("TODOIST_API_TOKEN")

        if not GOOGLE_SPREADSHEET_ID or GOOGLE_SPREADSHEET_ID == "YOUR_SPREADSHEET_ID":
            missing.append("GOOGLE_SPREADSHEET_ID")

        if missing:
            print("\n" + "=" * 50)
            print("ОШИБКА: Не настроены параметры в config.py:")
            for param in missing:
                print(f"  - {param}")
            print("\nСм. инструкции в папке Инструкции/")
            print("=" * 50 + "\n")
            return False

        return True

    except ImportError:
        print("\n" + "=" * 50)
        print("ОШИБКА: Файл config.py не найден!")
        print("\nСоздай его из config_example.py:")
        print("  cp config_example.py config.py")
        print("=" * 50 + "\n")
        return False


def test_integrations() -> dict:
    """
    Тестирует все интеграции

    Возвращает:
        dict с результатами тестов
    """

    results = {
        "todoist": False,
        "sheets": False,
        "telegram": False
    }

    print("\n" + "=" * 50)
    print("ТЕСТИРОВАНИЕ ИНТЕГРАЦИЙ")
    print("=" * 50)

    # 1. Тест Todoist
    print("\n1. Todoist API...")
    try:
        from todoist_integration import get_tasks
        tasks = get_tasks("all")
        print(f"   Найдено задач: {len(tasks)}")
        results["todoist"] = True
    except Exception as e:
        print(f"   Ошибка: {e}")

    # 2. Тест Google Sheets
    print("\n2. Google Sheets API...")
    try:
        from sheets_integration import read_tasks
        data = read_tasks(1)
        print(f"   Подключение успешно")
        results["sheets"] = True
    except Exception as e:
        print(f"   Ошибка: {e}")

    # 3. Тест Telegram (только проверка токена)
    print("\n3. Telegram Bot...")
    try:
        from config import TELEGRAM_BOT_TOKEN
        import requests
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        )
        if response.status_code == 200:
            bot_info = response.json()
            bot_name = bot_info.get("result", {}).get("username", "Unknown")
            print(f"   Бот: @{bot_name}")
            results["telegram"] = True
        else:
            print(f"   Ошибка токена: {response.status_code}")
    except Exception as e:
        print(f"   Ошибка: {e}")

    # Итог
    print("\n" + "-" * 50)
    print("РЕЗУЛЬТАТЫ:")
    for name, status in results.items():
        emoji = "✅" if status else "❌"
        print(f"  {emoji} {name.upper()}")
    print("-" * 50)

    return results


def quick_task(text: str) -> dict:
    """
    Быстрое создание задачи из командной строки

    Параметры:
        text: Текст задачи

    Возвращает:
        dict с результатами
    """

    from todoist_integration import create_task
    from sheets_integration import log_task

    result = {
        "success": False,
        "todoist_id": None,
        "sheets": False
    }

    try:
        # 1. Создаём в Todoist
        task = create_task(text)
        result["todoist_id"] = task.get("id")

        # 2. Логируем в Sheets
        result["sheets"] = log_task(
            task_text=text,
            source="CLI",
            user="Terminal",
            todoist_id=task.get("id", "N/A"),
            status="Created"
        )

        result["success"] = True

    except Exception as e:
        print(f"Ошибка: {e}")

    return result


def main():
    """Главная функция"""

    print("\n" + "=" * 50)
    print("СИСТЕМА API ИНТЕГРАЦИЙ")
    print("Модуль 6: CURSOR - Сотрудники под твой бизнес")
    print("=" * 50)

    # Проверяем конфигурацию
    if not check_config():
        sys.exit(1)

    # Парсим аргументы командной строки
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "test":
            # Тестируем интеграции
            test_integrations()

        elif command == "task":
            # Создаём задачу
            if len(sys.argv) > 2:
                task_text = " ".join(sys.argv[2:])
                print(f"\nСоздаю задачу: {task_text}")
                result = quick_task(task_text)
                if result["success"]:
                    print(f"Задача создана! ID: {result['todoist_id']}")
                else:
                    print("Ошибка создания задачи")
            else:
                print("Укажи текст задачи: python main.py task <текст>")

        elif command == "bot":
            # Запускаем бота
            print("\nЗапускаю Telegram бота...")
            from bot_example import main as run_bot
            run_bot()

        elif command == "setup":
            # Настройка (создание заголовков в Sheets)
            print("\nНастройка Google Sheets...")
            from sheets_integration import setup_headers
            setup_headers()

        else:
            print(f"\nНеизвестная команда: {command}")
            print("\nДоступные команды:")
            print("  test  — тестировать интеграции")
            print("  task  — создать задачу")
            print("  bot   — запустить Telegram бота")
            print("  setup — настроить Google Sheets")

    else:
        # Без аргументов — запускаем бота
        print("\nЗапускаю Telegram бота...")
        print("(Для других команд: python main.py --help)\n")

        from bot_example import main as run_bot
        run_bot()


if __name__ == "__main__":
    main()
