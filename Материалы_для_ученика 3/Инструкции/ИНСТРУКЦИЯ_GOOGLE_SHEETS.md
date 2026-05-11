# ИНСТРУКЦИЯ: Подключение Google Sheets API

> Модуль 6: API Интеграции
> Курс: CURSOR — Сотрудники под твой бизнес за вечер

---

## ОБЗОР

Google Sheets API требует больше настроек, чем Telegram или Todoist.
Но это самый мощный инструмент для логирования и аналитики!

**Что нужно сделать:**
1. Создать проект в Google Cloud
2. Включить Sheets API
3. Создать сервисный аккаунт
4. Скачать ключ (credentials.json)
5. Дать доступ к таблице

---

## ШАГ 1: Создание проекта в Google Cloud

### 1.1 Открой Google Cloud Console
1. Перейди на https://console.cloud.google.com
2. Войди в аккаунт Google

### 1.2 Создай новый проект
1. Нажми на селектор проектов (вверху слева)
2. Нажми **"Новый проект"**
3. Введи название: `Cursor-API-Integration`
4. Нажми **"Создать"**
5. Дождись создания (10-30 секунд)

### 1.3 Выбери проект
После создания убедись, что проект выбран (название отображается вверху).

---

## ШАГ 2: Включение Google Sheets API

### 2.1 Открой библиотеку API
1. В левом меню: **API и сервисы** → **Библиотека**
2. Или перейди: https://console.cloud.google.com/apis/library

### 2.2 Найди Google Sheets API
1. В поиске напиши: `Google Sheets API`
2. Нажми на карточку **Google Sheets API**

### 2.3 Включи API
1. Нажми кнопку **"Включить"**
2. Дождись активации

---

## ШАГ 3: Создание сервисного аккаунта

### 3.1 Открой раздел учётных данных
1. В левом меню: **API и сервисы** → **Учётные данные**
2. Или: https://console.cloud.google.com/apis/credentials

### 3.2 Создай сервисный аккаунт
1. Нажми **"+ Создать учётные данные"**
2. Выбери **"Сервисный аккаунт"**

### 3.3 Заполни данные
1. **Название:** `cursor-sheets-bot`
2. **ID:** оставь по умолчанию
3. **Описание:** `Бот для записи данных в Google Sheets`
4. Нажми **"Создать и продолжить"**

### 3.4 Роли (опционально)
Пропусти этот шаг — нажми **"Продолжить"**

### 3.5 Готово
Нажми **"Готово"**

---

## ШАГ 4: Скачивание ключа

### 4.1 Найди сервисный аккаунт
В списке **Сервисные аккаунты** найди созданный `cursor-sheets-bot`

### 4.2 Создай ключ
1. Нажми на email сервисного аккаунта
2. Перейди на вкладку **"Ключи"**
3. Нажми **"Добавить ключ"** → **"Создать новый ключ"**
4. Выбери формат **JSON**
5. Нажми **"Создать"**

### 4.3 Сохрани файл
Файл `credentials.json` скачается автоматически.

**ВАЖНО:**
- Переименуй файл в `credentials.json`
- Положи его в папку с кодом (рядом с `main.py`)
- **Никогда не коммить этот файл в git!**

---

## ШАГ 5: Создание Google Таблицы

### 5.1 Создай таблицу
1. Перейди на https://sheets.google.com
2. Нажми **"+ Пустой"**
3. Назови: `API Задачи`

### 5.2 Получи ID таблицы
Посмотри URL таблицы:
```
https://docs.google.com/spreadsheets/d/1ABC123XYZ789/edit
```

ID таблицы: `1ABC123XYZ789` (между `/d/` и `/edit`)

### 5.3 Сохрани ID
Вставь в `config.py`:
```python
GOOGLE_SPREADSHEET_ID = "1ABC123XYZ789"
```

---

## ШАГ 6: Доступ к таблице

### 6.1 Найди email сервисного аккаунта
Открой файл `credentials.json` и найди поле `client_email`:
```json
"client_email": "cursor-sheets-bot@cursor-api-integration.iam.gserviceaccount.com"
```

### 6.2 Дай доступ к таблице
1. Открой свою Google Таблицу
2. Нажми **"Поделиться"** (Share)
3. Вставь email сервисного аккаунта
4. Выбери роль **"Редактор"**
5. Убери галочку "Уведомить людей"
6. Нажми **"Поделиться"**

---

## ШАГ 7: Настройка config.py

### 7.1 Финальные настройки
```python
# Путь к файлу ключа
GOOGLE_CREDENTIALS_FILE = "credentials.json"

# ID твоей таблицы
GOOGLE_SPREADSHEET_ID = "1ABC123XYZ789"

# Название листа
GOOGLE_SHEET_NAME = "Лист1"  # или "Задачи"
```

---

## ШАГ 8: Тестирование

### 8.1 Создай заголовки
```bash
python main.py setup
```

### 8.2 Тест записи
```bash
python sheets_integration.py
```

### 8.3 Проверь таблицу
Открой Google Таблицу — там должны появиться данные!

---

## КАК РАБОТАЕТ API

### Запись данных
```python
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Авторизация
creds = Credentials.from_service_account_file('credentials.json')
service = build('sheets', 'v4', credentials=creds)

# Данные для записи
values = [["2024-01-15", "Тест", "Telegram"]]

# Запись
service.spreadsheets().values().append(
    spreadsheetId="ID_ТАБЛИЦЫ",
    range="Лист1!A:C",
    valueInputOption="USER_ENTERED",
    body={"values": values}
).execute()
```

### Чтение данных
```python
result = service.spreadsheets().values().get(
    spreadsheetId="ID_ТАБЛИЦЫ",
    range="Лист1!A:C"
).execute()

values = result.get('values', [])
for row in values:
    print(row)
```

---

## ЧАСТЫЕ ОШИБКИ

### Ошибка: "credentials.json not found"
**Причина:** Файл не найден
**Решение:** Положи `credentials.json` рядом с `main.py`

### Ошибка: "The caller does not have permission"
**Причина:** Нет доступа к таблице
**Решение:** Дай доступ сервисному аккаунту (ШАГ 6)

### Ошибка: "API not enabled"
**Причина:** Google Sheets API не включен
**Решение:** Включи API в Google Cloud Console (ШАГ 2)

### Ошибка: "Invalid spreadsheet ID"
**Причина:** Неверный ID таблицы
**Решение:** Проверь GOOGLE_SPREADSHEET_ID

### Ошибка: "Quota exceeded"
**Причина:** Превышен лимит запросов
**Решение:** Подожди 100 секунд или оптимизируй код

---

## ЛИМИТЫ API

| Параметр | Лимит |
|----------|-------|
| Запросов в минуту | 60 |
| Запросов в день | 500 (бесплатно) |
| Ячеек за запрос | 10,000,000 |

Для учебных целей — более чем достаточно!

---

## ДОКУМЕНТАЦИЯ

- Google Sheets API: https://developers.google.com/sheets/api
- Quickstart Python: https://developers.google.com/sheets/api/quickstart/python

---

## ФИНАЛ

Если всё настроено правильно, можешь запустить полную систему:

```bash
python main.py bot
```

Напиши боту текст → задача создастся в Todoist → данные залогируются в Sheets!
