# Инструкция: Создание Telegram бота через BotFather

> **Модуль 6:** API Интеграции  
> **Время:** 5-7 минут

---

## Что такое BotFather?

BotFather — официальный бот Telegram для создания и управления ботами. Все боты в Telegram создаются через него.

---

## Пошаговая инструкция

### Шаг 1: Найти BotFather

1. Открой Telegram (телефон или компьютер)
2. В поиске введи: `@BotFather`
3. Выбери бота с синей галочкой (верификация)
4. Нажми **Start** (или напиши `/start`)

```
Ты увидишь приветственное сообщение со списком команд
```

---

### Шаг 2: Создать нового бота

1. Напиши команду:
```
/newbot
```

2. BotFather спросит имя бота. Введи любое:
```
Cursor Helper
```
Это имя будет отображаться в чате.

3. BotFather спросит username. Введи уникальный:
```
cursor_helper_твоё_имя_bot
```

**Важно:** Username должен:
- Заканчиваться на `bot` или `_bot`
- Быть уникальным (не занятым)
- Состоять из латинских букв, цифр и подчёркиваний

---

### Шаг 3: Получить токен

После успешного создания BotFather пришлёт сообщение с токеном:

```
Done! Congratulations on your new bot. You will find it at t.me/your_bot_username.

Use this token to access the HTTP API:
1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ

Keep your token secure and store it safely.
```

**Скопируй токен** — это длинная строка после "Use this token to access the HTTP API:"

---

### Шаг 4: Сохранить токен

1. Открой файл `config_example.py`
2. Найди строку:
```python
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
```
3. Замени `YOUR_TELEGRAM_BOT_TOKEN_HERE` на свой токен:
```python
TELEGRAM_BOT_TOKEN = "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ"
```
4. Переименуй файл в `config.py`

---

### Шаг 5: Узнать свой Telegram ID

1. Найди в Telegram бота: `@userinfobot`
2. Напиши ему `/start`
3. Он ответит твоим ID:
```
Your ID: 123456789
```
4. Вставь этот ID в `config.py`:
```python
ADMIN_ID = 123456789
```

---

## Проверка

После настройки:
1. Найди своего бота в Telegram (по username)
2. Напиши ему `/start`
3. Пока бот не запущен — ответа не будет (это нормально)

---

## Решение проблем

### Username занят

**Проблема:** BotFather говорит "Sorry, this username is already taken"

**Решение:** Придумай другой username. Добавь:
- Свой ник: `cursor_helper_ivan_bot`
- Цифры: `cursor_helper_2026_bot`
- Проект: `my_project_helper_bot`

---

### Токен не скопировался полностью

**Проблема:** Бот не работает, ошибка "Unauthorized"

**Решение:** 
1. Напиши BotFather: `/token`
2. Выбери своего бота
3. Скопируй токен ещё раз (целиком!)
4. Проверь что в config.py токен в кавычках

---

### Не могу найти BotFather

**Проблема:** В поиске нет BotFather

**Решение:**
1. Проверь интернет-соединение
2. Попробуй прямую ссылку: https://t.me/BotFather
3. Убедись что ищешь именно `@BotFather` (с большой B и F)

---

## Команды BotFather

| Команда | Что делает |
|---------|-----------|
| `/newbot` | Создать нового бота |
| `/token` | Получить токен существующего бота |
| `/setname` | Изменить имя бота |
| `/setdescription` | Изменить описание |
| `/setabouttext` | Изменить "О боте" |
| `/setcommands` | Настроить меню команд |
| `/deletebot` | Удалить бота |

---

## Безопасность

⚠️ **ВАЖНО:**

1. **Никому не показывай токен** — кто знает токен, управляет ботом
2. **Не выкладывай config.py в GitHub** — добавь в .gitignore
3. **Если токен утёк** — сгенерируй новый через `/token`

---

> **Готово!** Теперь переходи к настройке AssemblyAI.
