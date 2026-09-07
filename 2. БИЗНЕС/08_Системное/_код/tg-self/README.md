# tg-self

Личный CLI к твоему Telegram. Это не бот: вход своим номером, сообщения от твоего имени.

Мандат для Cursor: [МАНДАТ.md](МАНДАТ.md) — что можно делать (как в телефоне), как голосовать и создавать опросы, когда писать. Писать в чужой чат — только по явной просьбе.

CLI: войти, диалоги, история, поиск, отправка, ответ, файл, опрос, голос, правка, удаление, реакция, закреп, прочитано, черновик, пересылка. Рецепты и запреты — в [МАНДАТ.md](МАНДАТ.md). «MSP | Рабочий чат» — только `--account work`.

## Один раз: ключи приложения

1. Открой [my.telegram.org](https://my.telegram.org) в браузере.
2. Войди тем же номером, что в Telegram.
3. API development tools → Create new application.
4. Скопируй `api_id` и `api_hash` в `.env` (не в чат).

Имя как у продукта, не как у бота: `MSProduct24`, кратко `MSProduct`. Не `tg-self` и не `tgself` — такие Telegram режет. Платформа: `Desktop` или `Android`.

## Установка

```bash
cd "2. БИЗНЕС/08_Системное/_код/tg-self"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполни `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` в `.env`.

## Команды

```bash
python3 tg.py login
python3 tg.py whoami
python3 tg.py dialogs
python3 tg.py send-saved "тест из Cursor"
python3 tg.py send --chat "Мачо Дача" --text "текст"
python3 tg.py send --account work --chat "MSP | Рабочий чат" --silent --text "текст"
python3 tg.py history --account work --chat "MSP | Рабочий чат" --limit 20
python3 tg.py search --account work --chat "MSP | Рабочий чат" --query "слово"
python3 tg.py polls --chat "Мачо Дача"
python3 tg.py vote --chat "Мачо Дача" --option "нет"
python3 tg.py poll --chat me --question "тест" --answers "Да" "Нет"
python3 tg.py react --account work --chat "MSP | Рабочий чат" --msg ID --emoji 👍
python3 tg.py edit --account work --chat "MSP | Рабочий чат" --msg ID --text "правка"
python3 tg.py delete --account work --chat "MSP | Рабочий чат" --msg ID

# Второй аккаунт (рабочий) — отдельная сессия tg_work.session
python3 tg.py login --account work
python3 tg.py whoami --account work
python3 tg.py dialogs --account work

# Третий аккаунт (обучение) — отдельная сессия tg_learn.session
python3 tg.py login --account learn
python3 tg.py whoami --account learn
python3 tg.py dialogs --account learn
```

`login` спросит телефон и код из Telegram. Если включён облачный пароль, спросит и его. После этого сессия лежит в `tg_self.session` и в git не попадает.

Если скрипт пишет «Сессия не авторизована» — файл `.session` может быть на месте, но Telegram отозвал сеанс (в приложении: Настройки → Устройства → Завершить другие сеансы) или из `.env` пропал `TELEGRAM_PHONE`. Перелогин: `python3 tg.py login --account self` (или `work`). Не перезапускать выгрузку вслепую.

## Анализ переписки

Корпус и производные лежат в `_выгрузки/` (в git не попадают).

```bash
# выгрузка сообщений в JSONL, инкрементально по state.json
python3 export_my_voice.py --account self --since 2026-01-01
python3 export_my_voice.py --account work --since 2026-01-01

# расшифровка голосовых (кэш в voice_cache.jsonl, повторно не платим)
python3 transcribe_voices.py --engine nexara --since 2026-01-01 --workers 8

# метрики стиля по трём кругам → _выгрузки/метрики_тона.md
python3 profile_stats.py --since 2026-01-01

# живые пары «входящее → мой ответ» по сценариям
python3 samples.py --circle work --scenario сроки --limit 10

# читаемая расшифровка одного чата под сборку карточки знаний
python3 chat_dump.py --title "New Martache" --since 2026-01-01 --out /tmp/mc.txt
```

Круги задаются файлами: `_выгрузки/close_circle.json` (близкий круг), `_выгрузки/skip_chats.json` (чаты вне выгрузки). Результат — профили тона в `_ПРОФИЛЬ/ТОН_ГОЛОСА_*.md`.

## Граница

Файл сессии равен полному доступу к аккаунту: не копируй его и не коммить. Писать в чужой чат — только если владелец явно попросил.
