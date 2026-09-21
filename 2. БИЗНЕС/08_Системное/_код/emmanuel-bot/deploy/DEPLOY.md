# Деплой Emmanuel Report Bot (Ubuntu + Docker)

## Важно

- Токен бота только в файле `.env` на сервере. **Канон: `@emmrov_bot` (id `8679755747`)**. Не путать с `@mshlykov_bot` — им этот контейнер уже поллился по ошибке, из‑за этого группа отвечала `chat not found`.
- Проверка после деплоя: в логах `bot @emmrov_bot id=8679755747`. Если другой username — сразу стоп.
- **MUTE (`OUTBOUND_MUTE=true`):** бот пишет только `ADMIN_TG_USER_ID` (личная переписка владельца с ботом). В группу «Копают рвы» и в чужие лички — ноль сообщений. Снимать только отдельным «можно в чат».
- **Сводка 00:00 в группу:** `#рвыотчёт` плюс жирные N из M. В Телеге по хэштегу виден ряд недель.
- **Администратор бота**: владелец `ADMIN_TG_USER_ID`; флаг `is_bot_admin` в таблице `users` тоже есть.
- **Зачёт отчёта:** в сообщении группы есть хэштег. Без `#` не считаем.
- **Имя в отчёте бота:** берём последний хэштег, которым человек уже подписывался в группе. Если не писал — имя из `seed/assigned_hashtags.json`. Спрашиваем «как подписывать» только если нет ни того ни другого. Леша пастор (`291663131`) вне круга отчёта, имя не спрашиваем.
- Состав группы сидируется из `seed/group_roster.json`, дальше ловим `chat_member`.

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
| `BOT_TOKEN` | токен `@emmrov_bot` из @BotFather |
| `GROUP_CHAT_ID` | `-1002125032114` (Копают рвы) |
| `OUTBOUND_MUTE` | `true` пока нельзя писать в чат |
| `JOBS_ENABLED` | `false` пока нельзя слать сводки по расписанию |
| `ADMIN_TG_USER_ID` | личный Telegram id владельца |

Бот должен быть **админом группы**, иначе не увидит все сообщения и вход/выход.

## 3. Запуск

```bash
docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

SQLite лежит в **Docker volume** `emmanuel_report_bot_data` (см. `docker-compose.yml`). Бэкап:  
`docker run --rm -v emmanuel-report-bot_emmanuel_report_bot_data:/from -v $(pwd):/to alpine tar czf /to/emmanuel-db-backup.tgz -C /from .`

URL в `.env`: `sqlite+aiosqlite:////data/emmanuel.sqlite3` — четыре слэша, иначе путь трактуется как относительный и БД не откроется.

Личные команды владельца в личке с ботом: `/start`, `/coverage`, `/sunday`. В группу бот при MUTE не отвечает.

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
python scripts/_deploy_to_vps.py
```

Перед деплоем: бэкап volume. После: в логе `@emmrov_bot` и `OUTBOUND_MUTE`.

## Часовой пояс

Все задачи расписания: **Europe/Moscow** (`app/scheduler.py`).

Сейчас при MUTE/JOBS_ENABLED=false наружу не уходят:

- вс 17:00 — напоминание в группу;
- вс 22:00 — личка;
- пн 10:00 — dropout;
- 00:00 / 20:00 — сводка покрытия (код есть, расписание выкл).

Дедлайн «вовремя» для счётчика серии — **понедельник 00:00 МСК** после отчётного воскресенья (см. `app/time_utils.py`).
