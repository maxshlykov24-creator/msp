# Деплой OpenClaw на VPS · через Cursor

> Используется в блоке 6 Модуля 3. **Главный путь — через Cursor Remote-SSH + один промпт агенту**. Ты не пишешь команды — пишешь задачу, агент Cursor выполняет.

## Главный путь · Cursor разворачивает за тебя

### Шаг 0 · что нужно перед деплоем

- VPS Timeweb Cloud-2 создан, IP записан, SSH-ключ работает (см. `ИНСТРУКЦИЯ_ДОСТУПЫ.md`).
- Локально workspace собран (Cursor прошёл по `ПРОМПТ_СБОРКИ.md`, в файлах workspace нет меток `🟡`).
- Локально мастер-тест проведён (см. `ВЫБОР_МОДЕЛИ.md` § 4) — выбраны primary + fallback модели.
- В руках 4 переменных для `.env`: `OPENROUTER_API_KEY`, `TG_BOT_TOKEN_LEAD_QUAL`, `MANAGER_CHAT_ID`, IP VPS.

### Шаг 1 · Cursor Remote-SSH к VPS

В Cursor: **Cmd+Shift+P** (Mac) / **Ctrl+Shift+P** (Win) → `Remote-SSH: Connect to Host...` → `ssh root@<IP_VPS>` → Enter.

Откроется **новое окно Cursor, подключённое к VPS**. В нижнем левом углу — `SSH: <IP>`.

В новом окне: **File → Open Folder** → `/root` или `/opt` → OK. Терминал (`Ctrl+\``) работает на VPS, чат Cursor (`Cmd+L`) тоже.

### Шаг 2 · Перенеси workspace и tools на VPS

С **локальной** машины (новая вкладка обычного терминала):

```bash
cd "<путь к StartPack>/_АГЕНТЫ/AI-Менеджер_1-й_линии"
rsync -avz --exclude='.env' --exclude='.git' \
  ./ root@<IP_VPS>:/opt/manager-bot/
```

⚠️ `.env` НЕ переносим — создадим на VPS отдельно.

### Шаг 3 · `.env` на VPS

В терминале Cursor (который на VPS):

```bash
nano /opt/manager-bot/.env
```

Вставь содержимое локального `.env` (Cmd+V). Сохрани: Ctrl+O → Enter → Ctrl+X.

### Шаг 4 · Один промпт Cursor-агенту

Открой Cursor Chat (`Cmd+L`), режим **Agent**, скопируй полный промпт из:

📄 **`StartPack_M3_addon/_АГЕНТЫ/AI-Менеджер_1-й_линии/ПРОМПТ_ДЕПЛОЯ.md`** → блок «Команда агенту»

Промпт содержит:
- задачу и входные данные
- 10 шагов (Node.js → OpenClaw → workspace → tools → configure → agents create → channels add → systemd → проверка)
- правила (показывать прогресс, останавливаться на ошибках, не выдумывать команды)

Cursor-агент выполнит каждый шаг сам, прокомментирует прогресс, остановится при ошибке.

⏱ Время выполнения: ~5 минут (включая `npm install -g openclaw` 30 секунд).

### Шаг 5 · Финальный тест

Когда агент скажет «всё готово»:
1. Открой Telegram → напиши своему боту `/start`. Должен ответить приветствием.
2. Прогони 5 диалогов из `MODE_LEAD_QUAL.md` (холодный, горячий, off-topic, стоп-вопрос, длинный) — проверь что бот ведёт себя по сценарию.
3. На горячем диалоге проверь что в чат менеджера (`MANAGER_CHAT_ID`) пришло summary через `escalate.sh`.

Если всё работает — **закрой ноут**. Gateway на VPS под `systemd`, переживает перезагрузку и крэш.

---

## Управление после деплоя

### Логи

```bash
# В терминале Cursor (на VPS) или через SSH:
journalctl -fu openclaw-gateway          # follow gateway логи
openclaw logs --tail 200                  # последние 200 строк через CLI
openclaw health                           # health check
openclaw doctor                           # авто-проверка
```

### Перезапуск (после изменений в openclaw.json или новых tools)

```bash
systemctl restart openclaw-gateway
```

### Правка workspace БЕЗ перезапуска

`AGENTS.md`, `IDENTITY.md`, `BOOTSTRAP.md`, `MODE_*.md` подхватываются **на каждый новый turn автоматически**. Просто `nano <file>` → правка → save. Перезапуск не нужен — это сильный плюс OpenClaw.

### Обновление workspace с локали

```bash
# С локальной машины (или прямо из Cursor Remote-SSH через rsync)
rsync -avz --exclude='.env' \
  ./workspace/ root@<IP_VPS>:/opt/manager-bot/workspace/
```

### Добавление второго агента (М4 Транскрибатор)

В чате Cursor на VPS — один промпт:

```
Добавь к существующему gateway второго агента transcriber:
- workspace: /opt/manager-bot/agents/transcriber/workspace
- config:    /opt/manager-bot/agents/transcriber/openclaw.config.json
- TG-токен:  $TG_BOT_TOKEN_TRANSCRIBER (уже в .env)
- канал:     telegram

Команды:
  openclaw agents create transcriber --workspace ... --config ...
  openclaw channels add telegram --token "$TG_BOT_TOKEN_TRANSCRIBER" --agent transcriber

Перезапуск gateway не нужен — он подхватит сам. Проверь openclaw agents list.
```

⏱ ~30 секунд. Это и есть масштаб OpenClaw — второй агент = одна команда.

---

## Fallback · если Cursor Remote-SSH не работает

Если Remote-SSH недоступен (старая версия Cursor, IT-ограничения, др.) — копипасть 9 команд руками через локальный SSH:

```bash
# 0. Подключение
ssh root@<IP>

# 1. Node.js 20+
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt update && apt install -y nodejs git nano
mkdir -p /opt/manager-bot

# 2. OpenClaw
npm install -g openclaw

# 3. Локально: rsync workspace на VPS (новая вкладка)
# rsync -avz --exclude='.env' ./ root@<IP>:/opt/manager-bot/

# 4. На VPS: chmod tools
chmod +x /opt/manager-bot/tools/*.sh

# 5. .env через nano
nano /opt/manager-bot/.env

# 6. Конфигурация — НЕ через interactive wizard, через config set
openclaw config set agents.defaults.workspace /opt/manager-bot
openclaw config set auth.providers.openrouter.apiKey "$OPENROUTER_API_KEY"
openclaw config set agents.defaults.model.primary "$OPENCLAW_MODEL_PRIMARY"
openclaw config set agents.defaults.model.fallbacks[0] "$OPENCLAW_MODEL_FALLBACK"
openclaw config set gateway.port 19000

# 7. Агент
openclaw agents create lead-qualifier \
  --workspace /opt/manager-bot/workspace \
  --config /opt/manager-bot/openclaw.config.example.json

# 8. Telegram
openclaw channels add telegram \
  --token "$TG_BOT_TOKEN_LEAD_QUAL" \
  --agent lead-qualifier

# 9. systemd
openclaw gateway install --systemd
systemctl daemon-reload && systemctl enable --now openclaw-gateway
```

⏱ ~10 минут руками. Cursor через Remote-SSH делает это за 5 минут и без ошибок.

---

## Если что-то не работает

См. `TROUBLESHOOTING_M3.md`. Самые частые проблемы:

- **`openclaw: command not found` после `npm install -g`** → `npm prefix -g` не в `$PATH`.
- **`Gateway failed to start: EADDRINUSE`** → порт 19000 занят.
- **`Bot doesn't respond after registration`** → `MANAGER_CHAT_ID` или TG-токен неправильный.
- **`401 Unauthorized` от OpenRouter** → ключ или баланс.
- **`Bot пишет, но не вызывает escalate`** → `chmod +x tools/*.sh` забыли.

В режиме Cursor Remote-SSH большинство этих проблем агент **сам диагностирует и чинит** на этапе деплоя — он читает вывод каждой команды и реагирует.

---

## Стоимость

- **VPS Timeweb Cloud-2:** ~600 ₽/мес (минимум для OpenClaw — gateway сам ест 500 MB).
- **OpenRouter** (Sonnet 4.6, 100 диалогов/день): ~$5–10/мес ≈ 500–900 ₽ через PDPAI.
- **Итого: ~1 100–1 500 ₽/мес** на одного агента 24/7.

Когда добавишь 2–3 агента (М4–М5) — **VPS тот же**, добавляются только LLM-токены. См. `VPS_РАСЧЁТ.md`.
