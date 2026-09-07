# Troubleshooting · Модуль 3 (OpenClaw)

> 12 типовых проблем, в которые упирались на сборке первого OpenClaw-агента. С каждой — диагностика и решение.

## Общий принцип диагностики

1. **Где смотрим логи?**
   - Локально: терминал, в котором запустил `openclaw gateway run`.
   - На VPS: `journalctl -fu openclaw-gateway` (`-f` follow, `-u` unit).
   - Утилита автодиагностики: `openclaw doctor` — пробежит чеклист.

2. **Что искать в логах?**
   - `Gateway listening on port 19000` — gateway успешно стартовал.
   - `Agent registered: <name>` — агент подцепился.
   - `Channel connected: <name>` — канал готов принимать сообщения.
   - `Polling for updates...` — Telegram-канал слушает.
   - `ERROR` / `WARN` — конкретная проблема, читать строку выше.

3. **Где править?**
   - 95% проблем — в **markdown workspace** (`AGENTS.md`, `IDENTITY.md`, `BOOTSTRAP.md`, `MODE_*.md`). Подхватываются на следующий turn без перезапуска.
   - 5% — в `openclaw.config.json` или `.env`. Требуют `systemctl restart openclaw-gateway`.

---

## 1. `openclaw: command not found` после `npm install -g`

### Причина
npm установил пакет, но путь `npm prefix -g` не в `$PATH`.

### Решение
**Mac:**
```bash
echo $PATH | tr ':' '\n' | grep -i node
# Если нет — добавь:
echo 'export PATH="$(npm prefix -g)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Windows:** Параметры → Система → Дополнительные параметры → Переменные среды → добавить путь, который вернул `npm prefix -g` (обычно `C:\Users\<ты>\AppData\Roaming\npm`). Перезапустить терминал.

---

## 2. Gateway не стартует: `EADDRINUSE` (port 19000 already in use)

### Причина
Уже запущен другой gateway, или порт занят другим процессом.

### Решение
```bash
# Найти кто держит порт
lsof -i :19000
# или (на Linux)
ss -tlnp | grep 19000

# Если это сам OpenClaw — корректно остановить
openclaw gateway stop

# Если другой процесс — kill <PID> или сменить порт
openclaw config set gateway.port 19001
```

---

## 3. Бот молчит в Telegram

### Симптомы
Написал боту → ответа нет. В логах — ничего нового после `Polling for updates...`.

### Диагностика
- Точно gateway запущен? `openclaw health` → `ok`?
- Точно тому боту пишешь? Открой профиль бота в TG, проверь username — совпадает с тем, что в логах `Channel connected: ...`?
- Ты написал боту хотя бы раз `/start`? Telegram блокирует исходящие до первого user-touch.

### Причины

**A. Опечатка в `TG_BOT_TOKEN_LEAD_QUAL`.**
- Симптом: в логах `TelegramUnauthorizedError` или `401`.
- Решение: проверь `.env`, перезапусти gateway.

**B. Канал зарегистрирован, но не привязан к агенту.**
- `openclaw channels list` → должен показать `agent: lead-qualifier` напротив телеграм-канала.
- Если `agent: (none)` — `openclaw channels link <channel-id> --agent lead-qualifier`.

**C. Telegram блокирует IP VPS.**
- Симптом: `TelegramNetworkError` периодически.
- Решение: попробуй другой регион VPS или прокси (см. п.12).

---

## 4. `409 Conflict: terminated by other getUpdates request`

### Причина
Где-то ещё работает второй экземпляр gateway/бота с **тем же** TG-токеном. Telegram пускает только одного.

### Решение
- Локально: Ctrl+C в терминале где запущен второй gateway.
- На VPS: `systemctl status openclaw-gateway` — точно один?
- Проверить, не остался ли zombie-процесс: `ps aux | grep openclaw`.

Альтернатива: создать **второй** TG-бот в BotFather специально для VPS (отдельный токен), локальный использовать как dev. Это самый чистый вариант для production.

---

## 5. `401 Unauthorized` от OpenRouter

### Симптом
В логах: `OpenRouter: 401 Unauthorized` или `Failed to call provider: anthropic/claude-sonnet-4.6`.

### Причины и решения

**A. Опечатка в `OPENROUTER_API_KEY`.**
- Проверь: ключ начинается с `sk-or-v1-`?
- Перевыпусти на openrouter.ai/keys.

**B. Нет баланса на OpenRouter.**
- Открой https://openrouter.ai/credits.
- Если 0$ — пополни через PDPAI (см. ИНСТРУКЦИЯ_ДОСТУПЫ).

**C. Проверка ключа на жизнь:**
```bash
curl https://openrouter.ai/api/v1/auth/key \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
# должен вернуть JSON с label и баланс
```

---

## 6. `404 Not Found` или модель не отвечает

### Причина
В `openclaw.config.json` или `.env` `MODEL_ID` — несуществующая или устаревшая модель.

### Решение
```bash
# Проверить актуальные ID
curl https://openrouter.ai/api/v1/models | jq '.data[] | select(.id|startswith("anthropic")) | .id'
```

Скопируй точный ID. Формат всегда `provider/model-name` (например `anthropic/claude-sonnet-4.6`).

⚠️ **Не использовать `*-latest` алиасы** — они часто дают 404 в API endpoint, работают только в UI.

---

## 7. Агент игнорирует workspace (отвечает «общими» словами)

### Симптом
Бот отвечает абстрактно, не использует факты из IDENTITY/BOOTSTRAP/MODE_*.md.

### Диагностика
```bash
openclaw agents inspect lead-qualifier
# покажет какие файлы workspace загружены и в каком порядке
```

### Причины

**A. Workspace path указан неверно.**
- В `openclaw.config.json` `workspace: "/opt/manager-bot/workspace"` — папка существует?
- `ls /opt/manager-bot/workspace/` — видны AGENTS.md, IDENTITY.md и т.д.?

**B. Не пройден `ПРОМПТ_СБОРКИ.md`.**
- В файлах остались жёлтые места `🟡` — Cursor не дописал.
- Открой каждый файл, проверь — все `🟡` должны быть заменены на текст из артефактов М2.

**C. Bootstrap не инжектится.**
- В `AGENTS.md` явно ссылайся на `@IDENTITY.md`, `@BOOTSTRAP.md`, `@MODE_LEAD_QUAL.md` — это OpenClaw синтаксис ссылки на workspace-файл.
- Если ссылок нет — модель не «увидит» эти файлы.

---

## 8. Tools не вызываются (бот не делает escalate / save_lead)

### Симптом
Лид прошёл квалификацию, бот сказал «передаю менеджеру» — но в чат менеджера ничего не пришло.

### Причины

**A. Tools не зарегистрированы.**
- В `openclaw.config.json` → `tools.registered` должен быть массив с реальными путями к `.sh`-файлам.
- Проверь: `ls -l /opt/manager-bot/tools/*.sh` → должны быть `-rwxr-xr-x` (executable).
- Если права не x — `chmod +x /opt/manager-bot/tools/*.sh`.

**B. `exec.ask: always` блокирует вызов.**
- Если `tools.exec.ask = always` — на каждый вызов tool gateway спрашивает оператора подтверждение. Для production надо `on-miss` (только незарегистрированные) или `never` (всё разрешено).
- Команда: `openclaw approvals approve <action-id>` для одобрения.

**C. Tool падает с ошибкой.**
- `tail -50 $TOOLS_LOG_PATH` — увидишь stderr попытки вызова.
- Чаще всего — переменные окружения не подхватились внутрь tool. Решение: в `.sh`-скрипте есть `set -a; . "$ROOT/.env"; set +a` — проверь что `.env` существует и `$ROOT` правильный.

**D. Промпт не учит вызывать tool.**
- В `AGENTS.md` § «Tools» должно быть явно: «когда `ready=true` — вызови `escalate(...)`».
- Если нет — модель не догадается. Допиши.

---

## 9. Logs показывают ошибку компактификации контекста

### Симптом
В логах: `Compaction failed: model returned non-JSON` или `Context too long, compaction triggered`.

### Причина
Workspace + история диалога превысили лимит контекста модели. OpenClaw попытался сжать (compaction) — не получилось.

### Решение
**A. Уменьши workspace** — длинные KB-блоки вынеси в отдельные файлы и сделай ссылки `@kb/file.md` вместо инжекта целиком.

**B. Смени модель compaction** в `openclaw.config.json`:
```json
"compaction": {
  "mode": "auto",
  "model": "anthropic/claude-sonnet-4-6"
}
```

**C. Ограничь длину диалога** в MODE-файле:
> «Не более 15 ходов в одном диалоге. После — escalate(reason='dialog_too_long')».

---

## 10. Workspace меняется, но бот отвечает по-старому

### Симптом
Изменил `IDENTITY.md` или `MODE_LEAD_QUAL.md` — бот всё равно отвечает как раньше.

### Причины

**A. OpenClaw кеширует workspace на сессию.**
- Решение: `openclaw agents reload lead-qualifier` (мягкая перезагрузка).
- Если не помогло — `systemctl restart openclaw-gateway`.

**B. Memory module хранит старый контекст.**
- Если `memory.enabled: true` в конфиге — диалог с лидом помнит старые правила.
- Решение: для тестов — `openclaw memory clear --agent lead-qualifier --user <tg_id>`.

**C. Lid пишет в том же диалоге, что и до правки.**
- Memory держит state. Скажи лиду `/start` — это сбросит локальный state.

---

## 11. Heartbeat дёргается чаще, чем нужно (engagement-роль)

### Симптом
Тренер команды (роль 6) отправляет пятничный пинг каждый час, а не раз в неделю.

### Причина
В `HEARTBEAT.md` не указано конкретное расписание, OpenClaw fallback-ит к default (1 час).

### Решение
В конфиге агента:
```json
"heartbeat": {
  "enabled": true,
  "every": "1d",
  "activeHours": {
    "start": "16:00",
    "end": "16:30"
  }
}
```

Или жёстко через `openclaw cron`:
```bash
openclaw cron add \
  --agent engagement \
  --cron "0 16 * * 5" \
  --task "Запусти MODE_ENGAGEMENT.md для всей команды"
```

---

## 12. Telegram режет IP VPS

### Симптом
Локально gateway работает. На VPS — `TelegramNetworkError` при попытке `getUpdates`.

### Причина
Некоторые РФ-провайдеры VPS периодически попадают под блокировки IP-пула Telegram. Реже на Timeweb, чаще на дешёвых.

### Решение
**A. Сменить регион VPS** в Timeweb (есть Москва, Питер, Казахстан). На Казахстан почти всегда работает.

**B. Использовать прокси для Telegram-канала.** В конфиге канала:
```bash
openclaw channels add telegram \
  --token "$TG_BOT_TOKEN_LEAD_QUAL" \
  --agent lead-qualifier \
  --proxy "socks5://USER:PASS@PROXY:PORT"
```

**C. Webhook вместо polling.** Сложнее, но надёжнее: бот не «спрашивает» Telegram, а Telegram сам шлёт ему. Требует HTTPS-домена и nginx. Это уровень М10 — сейчас не лезем.

---

## Если всё совсем плохо

1. **`openclaw doctor`** — авто-проверка типовых проблем, выдаст список `❌` пунктов.
2. **`openclaw logs --tail 200`** — последние 200 строк gateway-лога.
3. Проверь видео урока — точно ли все шаги пройдены.
4. Скинь в чат ученикам ошибку с пометкой «М3-OpenClaw, шаг X» — Лина или коллеги ответят за пару часов.
5. Запиши Loom 1 минута: показал экран с логами + что делал → отправь в форму поддержки. Вебстраж разберётся за 24 часа.

⚠️ **Чего НЕ делать:**
- Не публикуй полный TG-токен или OPENROUTER_API_KEY в чатах. Закрой `XXX...` последние 4 символа.
- Не «всё снести и переустановить» при первой ошибке. 95% случаев — мелочь в `.env` или workspace.
- Не правь `node_modules` руками — переустанови `npm install -g openclaw`.
