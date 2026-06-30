# Деплой dg-questions-bot

## Первый деплой

### 1. Подготовь .env локально

```bash
cp .env.example .env
# отредактируй .env — вставь BOT_TOKEN
```

`.env`:
```
BOT_TOKEN=8894846148:AAFO8gqF_v2HaeONkdPAxTUETSNwhMzsiAY
DATABASE_URL=sqlite+aiosqlite:////data/dg.sqlite3
DECK_ID=main
LOG_LEVEL=INFO
```

### 2. Сгенерируй questions.json (если колода изменилась)

```bash
python build_questions.py
```

### 3. Задеплой на VPS

```bash
DEPLOY_SSH_PASSWORD='cRFcMaUMow1PD+' python scripts/_deploy_to_vps.py
```

Скрипт:
- Пакует проект в tgz (без `.env` в git — передаётся отдельно в архив)
- Загружает на `72.56.123.137` в `/opt/dg-questions-bot`
- Пересобирает Docker-образ и запускает

---

## Повторный деплой (обновление кода)

```bash
DEPLOY_SSH_PASSWORD='cRFcMaUMow1PD+' python scripts/_deploy_to_vps.py
```

БД сохраняется в Docker volume `dg_data` и не теряется при редеплое.

---

## Ручное управление на сервере

```bash
ssh root@72.56.123.137

cd /opt/dg-questions-bot

docker compose logs -f          # смотреть логи в реальном времени
docker compose ps                # статус контейнера
docker compose restart           # перезапустить без пересборки
docker compose down && docker compose up -d  # полный перезапуск
```

---

## Бэкап БД

```bash
# Скопировать sqlite на локальную машину
scp root@72.56.123.137:/var/lib/docker/volumes/dg-questions-bot_dg_data/_data/dg.sqlite3 ./backup.sqlite3
```

---

## Первичная установка Docker на сервере (однократно)

```bash
ssh root@72.56.123.137
apt update && apt install -y docker.io docker-compose-plugin
```

---

## Структура на сервере

```
/opt/dg-questions-bot/
├── app/
├── questions.json
├── Dockerfile
├── docker-compose.yml
├── .env          ← только на сервере, не в git
└── data/         ← создаётся скриптом (volume point)
```
