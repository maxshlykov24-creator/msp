"""
=============================================================================
GOOGLE SHEETS API - ЛОГИРОВАНИЕ ДАННЫХ
=============================================================================
Модуль 6: API Интеграции
Курс: CURSOR - Сотрудники под твой бизнес за вечер

Этот модуль:
1. Записывает данные в Google Sheets
2. Читает данные из таблицы
3. Логирует все созданные задачи
=============================================================================
"""

import os
from datetime import datetime

# Google Sheets API
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Импортируем конфигурацию
try:
    from config import (
        GOOGLE_CREDENTIALS_FILE,
        GOOGLE_SPREADSHEET_ID,
        GOOGLE_SHEET_NAME
    )
except ImportError:
    print("ОШИБКА: Создай файл config.py из config_example.py")
    GOOGLE_CREDENTIALS_FILE = None
    GOOGLE_SPREADSHEET_ID = None
    GOOGLE_SHEET_NAME = "Задачи"

# Области доступа для Google Sheets API
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_sheets_service():
    """
    Создаёт и возвращает сервис Google Sheets API

    Возвращает:
        Resource объект для работы с API
    """

    if not GOOGLE_CREDENTIALS_FILE or not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Файл credentials.json не найден.\n"
            f"Скачай его из Google Cloud Console и положи рядом со скриптом."
        )

    # Загружаем учётные данные
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=SCOPES
    )

    # Создаём сервис
    service = build('sheets', 'v4', credentials=creds)

    return service


def log_task(
    task_text: str,
    source: str = "Manual",
    user: str = "Unknown",
    todoist_id: str = "N/A",
    status: str = "Created"
) -> bool:
    """
    Записывает информацию о задаче в Google Sheets

    Параметры:
        task_text: Текст задачи
        source: Источник (Telegram, Web, Manual)
        user: Имя пользователя
        todoist_id: ID задачи в Todoist
        status: Статус задачи

    Возвращает:
        True если успешно
    """

    if not GOOGLE_SPREADSHEET_ID:
        print("GOOGLE_SPREADSHEET_ID не настроен в config.py")
        return False

    try:
        service = get_sheets_service()

        # Текущая дата и время
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Данные для записи
        values = [[
            timestamp,      # Дата и время
            task_text,      # Текст задачи
            source,         # Источник
            user,           # Пользователь
            todoist_id,     # ID в Todoist
            status          # Статус
        ]]

        # Диапазон для записи (добавляем в конец)
        range_name = f"{GOOGLE_SHEET_NAME}!A:F"

        body = {
            'values': values
        }

        # Добавляем строку в таблицу
        result = service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()

        print(f"Залогировано в Google Sheets: {task_text[:30]}...")
        return True

    except HttpError as error:
        print(f"Ошибка Google Sheets API: {error}")
        return False
    except Exception as e:
        print(f"Ошибка: {e}")
        return False


def read_tasks(limit: int = 10) -> list:
    """
    Читает последние задачи из Google Sheets

    Параметры:
        limit: Максимальное количество задач

    Возвращает:
        list строк из таблицы
    """

    if not GOOGLE_SPREADSHEET_ID:
        print("GOOGLE_SPREADSHEET_ID не настроен в config.py")
        return []

    try:
        service = get_sheets_service()

        # Читаем данные
        range_name = f"{GOOGLE_SHEET_NAME}!A:F"

        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SPREADSHEET_ID,
            range=range_name
        ).execute()

        values = result.get('values', [])

        if not values:
            print("Таблица пуста")
            return []

        # Возвращаем последние N строк (без заголовка)
        return values[-limit:] if len(values) > limit else values[1:]

    except HttpError as error:
        print(f"Ошибка Google Sheets API: {error}")
        return []


def setup_headers() -> bool:
    """
    Создаёт заголовки в таблице (вызови один раз при настройке)

    Возвращает:
        True если успешно
    """

    if not GOOGLE_SPREADSHEET_ID:
        print("GOOGLE_SPREADSHEET_ID не настроен в config.py")
        return False

    try:
        service = get_sheets_service()

        # Заголовки столбцов
        headers = [[
            "Дата и время",
            "Задача",
            "Источник",
            "Пользователь",
            "Todoist ID",
            "Статус"
        ]]

        range_name = f"{GOOGLE_SHEET_NAME}!A1:F1"

        body = {
            'values': headers
        }

        result = service.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()

        print("Заголовки созданы!")
        return True

    except HttpError as error:
        print(f"Ошибка Google Sheets API: {error}")
        return False


def get_stats() -> dict:
    """
    Получает статистику по задачам

    Возвращает:
        dict со статистикой
    """

    tasks = read_tasks(limit=1000)

    if not tasks:
        return {"total": 0}

    # Считаем статистику
    stats = {
        "total": len(tasks),
        "by_source": {},
        "by_status": {},
        "today": 0
    }

    today = datetime.now().strftime("%Y-%m-%d")

    for task in tasks:
        if len(task) >= 6:
            # По источнику
            source = task[2] if len(task) > 2 else "Unknown"
            stats["by_source"][source] = stats["by_source"].get(source, 0) + 1

            # По статусу
            status = task[5] if len(task) > 5 else "Unknown"
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # За сегодня
            if task[0].startswith(today):
                stats["today"] += 1

    return stats


# =============================================================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# =============================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("Тестирование Google Sheets API")
    print("=" * 50)

    # 1. Создаём заголовки (один раз)
    print("\n1. Создаём заголовки...")
    setup_headers()

    # 2. Логируем тестовую задачу
    print("\n2. Логируем задачу...")
    log_task(
        task_text="Тестовая задача из Python",
        source="Test",
        user="Developer",
        todoist_id="12345",
        status="Created"
    )

    # 3. Читаем последние задачи
    print("\n3. Читаем задачи...")
    tasks = read_tasks(5)
    for task in tasks:
        print(f"   {task}")

    # 4. Получаем статистику
    print("\n4. Статистика:")
    stats = get_stats()
    print(f"   Всего задач: {stats['total']}")
    print(f"   За сегодня: {stats['today']}")
    print(f"   По источникам: {stats['by_source']}")

    print("\n" + "=" * 50)
    print("Тест завершён!")
