# ИНСТРУКЦИЯ: Подключение Todoist API

> Модуль 6: API Интеграции
> Курс: CURSOR — Сотрудники под твой бизнес за вечер

---

## ШАГ 1: Регистрация в Todoist

### 1.1 Создай аккаунт
1. Перейди на https://todoist.com
2. Зарегистрируйся (бесплатно) или войди

### 1.2 Базовая настройка
1. Создай проект "API Задачи" (опционально)
2. Запомни его название

---

## ШАГ 2: Получение API токена

### 2.1 Открой настройки
1. В Todoist нажми на своё имя (слева внизу)
2. Выбери **Настройки** (Settings)

### 2.2 Найди раздел "Интеграции"
1. В левом меню выбери **Интеграции** (Integrations)
2. Прокрути вниз до **Токен API для разработчиков**

### 2.3 Скопируй токен
Токен выглядит так:
```
a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**Этот токен секретный!** Не показывай его никому.

---

## ШАГ 3: Настройка config.py

### 3.1 Вставь токен
Открой `config.py` и замени:
```python
TODOIST_API_TOKEN = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
```

### 3.2 Укажи проект (опционально)
Если хочешь, чтобы задачи создавались в конкретном проекте:

1. Открой проект в Todoist
2. Посмотри URL: `todoist.com/app/project/2345678901`
3. Скопируй ID проекта (число)
4. Вставь в config.py:
```python
TODOIST_PROJECT_ID = "2345678901"
```

Если оставить `None` — задачи будут создаваться во "Входящих".

---

## ШАГ 4: Тестирование API

### 4.1 Тест через Python
```bash
python todoist_integration.py
```

Ты увидишь:
```
=================================================
Тестирование Todoist API
=================================================

1. Создаём задачу...
   ID: 8234567890
   URL: https://todoist.com/showTask?id=8234567890

2. Получаем задачи на сегодня...
   - Тестовая задача из Python
   - ...

=================================================
Тест завершён!
```

### 4.2 Проверь в Todoist
Открой Todoist — там должна появиться тестовая задача!

---

## КАК РАБОТАЕТ API

### Создание задачи (POST)
```python
import requests

url = "https://api.todoist.com/rest/v2/tasks"
headers = {
    "Authorization": "Bearer ВАШ_ТОКЕН",
    "Content-Type": "application/json"
}
data = {
    "content": "Текст задачи",
    "due_string": "today",
    "priority": 2
}

response = requests.post(url, headers=headers, json=data)
task = response.json()
print(task["id"])  # ID созданной задачи
```

### Получение задач (GET)
```python
url = "https://api.todoist.com/rest/v2/tasks"
params = {"filter": "today"}

response = requests.get(url, headers=headers, params=params)
tasks = response.json()

for task in tasks:
    print(task["content"])
```

### Завершение задачи (POST)
```python
task_id = "8234567890"
url = f"https://api.todoist.com/rest/v2/tasks/{task_id}/close"

response = requests.post(url, headers=headers)
# Статус 204 = успех
```

---

## ПРИОРИТЕТЫ В TODOIST

| Значение | Цвет | Описание |
|----------|------|----------|
| 1 | Без цвета | Обычный |
| 2 | Синий | Низкий |
| 3 | Оранжевый | Средний |
| 4 | Красный | Высокий |

```python
create_task("Срочная задача!", priority=4)  # Красный
```

---

## СРОКИ (due_string)

Todoist понимает естественный язык:

| Строка | Значение |
|--------|----------|
| `today` | Сегодня |
| `tomorrow` | Завтра |
| `next monday` | Следующий понедельник |
| `in 3 days` | Через 3 дня |
| `every monday` | Каждый понедельник |
| `Jan 15` | 15 января |

```python
create_task("Еженедельный отчёт", due_string="every friday at 17:00")
```

---

## ЧАСТЫЕ ОШИБКИ

### Ошибка 401: Unauthorized
**Причина:** Неверный токен
**Решение:** Проверь TODOIST_API_TOKEN в config.py

### Ошибка 403: Forbidden
**Причина:** Токен не имеет прав
**Решение:** Получи новый токен в настройках Todoist

### Ошибка 404: Not Found
**Причина:** Неверный ID проекта или задачи
**Решение:** Проверь TODOIST_PROJECT_ID

### Задача создаётся, но не в том проекте
**Причина:** Не указан или неверный project_id
**Решение:** Укажи правильный TODOIST_PROJECT_ID

---

## ДОКУМЕНТАЦИЯ

Полная документация Todoist REST API:
https://developer.todoist.com/rest/v2/

---

## СЛЕДУЮЩИЙ ШАГ

Переходи к настройке Google Sheets:
→ `ИНСТРУКЦИЯ_GOOGLE_SHEETS.md`
