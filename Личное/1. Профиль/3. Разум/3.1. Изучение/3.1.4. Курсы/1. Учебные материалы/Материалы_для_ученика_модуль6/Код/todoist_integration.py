"""
=============================================================================
TODOIST API - ИНТЕГРАЦИЯ С ТАСК-МЕНЕДЖЕРОМ
=============================================================================
Модуль 6: API Интеграции
Курс: CURSOR - Сотрудники под твой бизнес за вечер

Этот модуль:
1. Создаёт задачи в Todoist
2. Получает список задач
3. Завершает задачи
=============================================================================
"""

import requests
from datetime import datetime

# Импортируем конфигурацию
try:
    from config import TODOIST_API_TOKEN, TODOIST_PROJECT_ID
except ImportError:
    print("ОШИБКА: Создай файл config.py из config_example.py")
    TODOIST_API_TOKEN = None
    TODOIST_PROJECT_ID = None

# Базовый URL API Todoist
TODOIST_API_URL = "https://api.todoist.com/rest/v2"


def get_headers() -> dict:
    """Возвращает заголовки для запросов к API"""
    return {
        "Authorization": f"Bearer {TODOIST_API_TOKEN}",
        "Content-Type": "application/json"
    }


def create_task(content: str, due_string: str = "today", priority: int = 1) -> dict:
    """
    Создаёт задачу в Todoist

    Параметры:
        content: Текст задачи
        due_string: Срок выполнения (today, tomorrow, next monday, etc.)
        priority: Приоритет 1-4 (4 = самый высокий)

    Возвращает:
        dict с данными созданной задачи
    """

    if not TODOIST_API_TOKEN:
        raise ValueError("TODOIST_API_TOKEN не настроен в config.py")

    url = f"{TODOIST_API_URL}/tasks"

    # Данные для создания задачи
    data = {
        "content": content,
        "due_string": due_string,
        "priority": priority
    }

    # Если указан проект — добавляем его
    if TODOIST_PROJECT_ID:
        data["project_id"] = TODOIST_PROJECT_ID

    # Отправляем POST запрос
    response = requests.post(url, headers=get_headers(), json=data)

    # Проверяем ответ
    if response.status_code == 200:
        task = response.json()
        print(f"Задача создана: {task.get('id')} — {content[:30]}...")
        return task
    else:
        print(f"Ошибка создания задачи: {response.status_code}")
        print(response.text)
        raise Exception(f"Todoist API error: {response.status_code}")


def get_tasks(filter_string: str = "today") -> list:
    """
    Получает список задач из Todoist

    Параметры:
        filter_string: Фильтр задач (today, overdue, all, etc.)

    Возвращает:
        list задач
    """

    if not TODOIST_API_TOKEN:
        raise ValueError("TODOIST_API_TOKEN не настроен в config.py")

    url = f"{TODOIST_API_URL}/tasks"

    # Параметры запроса
    params = {"filter": filter_string}

    # Отправляем GET запрос
    response = requests.get(url, headers=get_headers(), params=params)

    if response.status_code == 200:
        tasks = response.json()
        print(f"Получено задач: {len(tasks)}")
        return tasks
    else:
        print(f"Ошибка получения задач: {response.status_code}")
        raise Exception(f"Todoist API error: {response.status_code}")


def complete_task(task_id: str) -> bool:
    """
    Завершает задачу в Todoist

    Параметры:
        task_id: ID задачи

    Возвращает:
        True если успешно
    """

    if not TODOIST_API_TOKEN:
        raise ValueError("TODOIST_API_TOKEN не настроен в config.py")

    url = f"{TODOIST_API_URL}/tasks/{task_id}/close"

    # Отправляем POST запрос
    response = requests.post(url, headers=get_headers())

    if response.status_code == 204:
        print(f"Задача {task_id} завершена")
        return True
    else:
        print(f"Ошибка завершения задачи: {response.status_code}")
        return False


def delete_task(task_id: str) -> bool:
    """
    Удаляет задачу из Todoist

    Параметры:
        task_id: ID задачи

    Возвращает:
        True если успешно
    """

    if not TODOIST_API_TOKEN:
        raise ValueError("TODOIST_API_TOKEN не настроен в config.py")

    url = f"{TODOIST_API_URL}/tasks/{task_id}"

    # Отправляем DELETE запрос
    response = requests.delete(url, headers=get_headers())

    if response.status_code == 204:
        print(f"Задача {task_id} удалена")
        return True
    else:
        print(f"Ошибка удаления задачи: {response.status_code}")
        return False


# =============================================================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# =============================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("Тестирование Todoist API")
    print("=" * 50)

    # 1. Создаём тестовую задачу
    print("\n1. Создаём задачу...")
    task = create_task(
        content="Тестовая задача из Python",
        due_string="today",
        priority=2
    )
    print(f"   ID: {task.get('id')}")
    print(f"   URL: {task.get('url')}")

    # 2. Получаем список задач
    print("\n2. Получаем задачи на сегодня...")
    tasks = get_tasks("today")
    for t in tasks[:5]:
        print(f"   - {t.get('content')}")

    # 3. Завершаем задачу (раскомментируй если нужно)
    # print("\n3. Завершаем задачу...")
    # complete_task(task.get('id'))

    print("\n" + "=" * 50)
    print("Тест завершён!")
