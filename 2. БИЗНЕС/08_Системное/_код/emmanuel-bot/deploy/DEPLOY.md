# Деплой Emmanuel Report Bot (Ubuntu + Docker)

## Важно

- Токен бота только в файле `.env` на сервере.
- **Администратор бота**: тот пользователь (в личке), который первым успешно нажимает `/start` при пустой базе. Флаг хранится в таблице `users` (`is_bot_admin`). Команда `/admin` показывает статус. В Telegram-группе это не «админ чата», а точка входа для будущих админских функций и договорённостей по боту.
- **Нагрузка**: до ~30 активных человек SQLite на одном томе более чем достаточно.
- Личные напоминания в воскресенье 22:00 получат только пользователи, которые **хоть раз написали** боту `/start` и состоят в группе (`GROUP_CHAT_ID`). Telegram не отдаёт полный список участников чата.

## 1. Подготовка на сервере

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo mkdir -p /opt/emmanuel-bot && sudo chown "$USER:$USER" /opt/emmanuel-bot
cd /opt/emmanuel-bot
```

Скопируй каталог проекта `emmanuel-bot/` на сервер (git clone/rsync/scp).

## 2. Конфиг

```bash
cd emmanuel-bot
cp .env.example .env
mkdir -p data
nano .env
```

Заполнить:

| Переменная | Пример |
|------------|--------|
| `BOT_TOKEN` | из @BotFather |
| `GROUP_CHAT_ID` | `-1001234567890` для боевого режима; `0` — тест без группы (нет проверки участия и нет сообщений в чат из расписания) |

Узнать ID группы: временно отправить сообщение ботом в группу и посмотреть `chat.id` в логах, либо бот-помощники.

Бот должен быть **добавлен в группу** и иметь возможность отправлять туда сообщения.

## 3. Запуск

```bash
docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

SQLite лежит в **Docker volume** `emmanuel_report_bot_data` (см. `docker-compose.yml`). Бэкап:  
`docker run --rm -v emmanuel-report-bot_emmanuel_report_bot_data:/from -v $(pwd):/to alpine tar czf /to/emmanuel-db-backup.tgz -C /from .`

URL в `.env`: `sqlite+aiosqlite:////data/emmanuel.sqlite3` — четыре слэша, иначе путь трактуется как относительный и БД не откроется.


## Сервер (с 2026-07-31)

| | |
|--|--|
| VPS | **DKAcademy / ai-msp** `194.87.226.234` |
| SSH | `ssh -i ~/.ssh/dkacademy_analytics_deploy root@194.87.226.234` (алиас `dkacademy-vps`) |
| Каталог | `/opt/emmanuel-report-bot` |
| Контейнер | `emmanuel-report-bot`, volume `emmanuel-report-bot_emmanuel_report_bot_data` |
| Лимит RAM | 180 МБ |

**Было до 31.07.2026:** LicenseBridge-хаб `72.56.123.137` — снято из‑за нехватки памяти. На том же VPS — `dkacademy-bot`, analytics, сайт; бот порты не открывает (long polling).

Повторный деплой:

```bash
cd "2. БИЗНЕС/08_Системное/_код/emmanuel-bot"
# ключ по умолчанию — dkacademy_analytics_deploy
python scripts/_deploy_to_vps.py
# или: DEPLOY_SSH_KEY="$HOME/.ssh/dkacademy_analytics_deploy" python scripts/_deploy_to_vps.py
```


## Часовой пояс

Все задачи расписания: **Europe/Moscow** (`app/scheduler.py`).

Таймеры:

- вс 17:00 — напоминание в группу;
- вс 22:00 — напоминание в личку тем, у кого нет отчёта за текущую отчётную неделю;
- пн 09:00 — дайджест по прошедшей неделе.

Дедлайн «вовремя» для счётчика серии — **понедельник 00:00 МСК** после отчётного воскресенья (см. `app/time_utils.py`).
