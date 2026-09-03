# dg-questions-bot

Telegram-бот для вечеров вопросов с лидерами домашних групп.

**Два телефона:**
- 🎯 **Модератор** — управляет темпом, отмечает ответы, видит прогресс
- 📺 **Экран для стола** — показывает вопрос, передаётся по кругу

**Гарантии:**
- Вопросы не повторяются между встречами (DeckLedger)
- Порядок: сначала лёгкие → тёплые → чуть глубже
- Состояние переживает перезапуск бота

---

## Быстрый старт

```bash
# 1. Зависимости
pip install -r requirements.txt

# 2. Настройка
cp .env.example .env
# вставь BOT_TOKEN в .env

# 3. Сгенерировать questions.json из колоды
python build_questions.py

# 4. Запуск локально (для теста)
python -m app.main
```

---

## Деплой на VPS

См. `deploy/DEPLOY.md`.

```bash
DEPLOY_SSH_PASSWORD='...' python scripts/_deploy_to_vps.py
```

---

## Игровой цикл

```
/start → выбор роли
Модератор: создаёт сессию → получает код
Экран: вводит код → связываются

Модератор: выбирает Продолжить/Сначала → Встреча 1 / Полная колода

Экран: [▶ Получить вопрос] → вопрос показывается обоим
  → [↺ Заменить (1 раз)] — заменить вопрос
  → ожидает модератора

Модератор: [✅ Ответил] / [⏭ Пропустить] / [↩ Отмена]
  → экран снова активен

Модератор: [🏁 Завершить встречу] → итог + запись в колоду
```

---

## Стек

- **Python 3.12**, **aiogram 3**, **SQLAlchemy 2 + aiosqlite**
- **pydantic-settings** — конфиг из .env
- **Docker** — деплой на VPS 72.56.123.137
- **SQLite** — хранение в Docker volume (переживает редеплой)

---

## Структура

```
app/
  bot.py        — все хендлеры
  models.py     — GameSession, SessionQuestion, DeckLedger
  questions.py  — загрузка колоды, движок порядка
  keyboards.py  — inline-клавиатуры
  config.py     — pydantic-settings
  database.py   — engine / session factory
  main.py       — точка входа

build_questions.py  — парсер колоды .md → questions.json
questions.json       — сгенерированная колода (193 вопроса)
```
