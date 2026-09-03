# LicenseBridge USA — RUNBOOK

Как устроена боевая система, где живёт код, как выкладывать и что смотреть при поломке.
Доступы и секреты — `ДОСТУПЫ.md` и `ДОСТУПЫ_Telnyx.md`, здесь их нет.

**Обновлено:** 2026-09-03

---

## 1. Что где работает

| Сервис | Сервер | Путь | Контейнеры |
|---|---|---|---|
| Хаб: приём заявок, антидубли, распределение, журнал звонков, алерты | `72.56.123.137` (Timeweb) | `/opt/licensebridge-tilda-webhook` | `lb-hub-api`, `lb-hub-worker`, `lb-hub-db`, `lb-hub-caddy` |
| Дашборд `lb.mspod24.ru` | тот же | `/opt/licensebridge-analytics` | `lb-analytics` |
| Телефония Asterisk + FreePBX | `159.65.97.156` (DigitalOcean) | `/etc/asterisk/` | systemd `asterisk` |

Домены обслуживает Caddy хаба, сертификаты выпускаются автоматически.

---

## 2. Где живёт код

Источник правды по коду — **git этого vault**, но исторически правки делались прямо на
сервере, поэтому перед любой выкладкой проверяем, что локальная копия не отстала.

| Что | Путь в vault |
|---|---|
| Хаб | `04_Производство/Активные/LicenseBridge_USA/tilda-webhook/` |
| Дашборд | `08_Системное/_код/licensebridge-analytics/` |
| Конфиги Asterisk | `04_Производство/Активные/LicenseBridge_USA/asterisk/etc/` |
| Звуки блокеров и IVR | `04_Производство/Активные/LicenseBridge_USA/asterisk/sounds/` |

### Снять код с сервера

```bash
cd "2. БИЗНЕС/04_Производство/Активные/LicenseBridge_USA"
rsync -av --delete --exclude='.env' --exclude='.env.bak*' --exclude='__pycache__' \
  licensebridge-hub:/opt/licensebridge-tilda-webhook/{app,scripts,tests,migrations,docker-compose.yml,Dockerfile,Caddyfile,requirements.txt,.env.example} \
  tilda-webhook/
```

Перед снятием кода на сервере всегда есть свежие `app.bak-*` и `.env.bak-*` — это
ручные бэкапы прошлых правок, в vault они не нужны.

---

## 3. Выкладка

**Правило: `.env` на сервере не перезаписываем никогда.** Там флаги, токены, номера
линий и часы смен, которые меняются на живом сервисе. Один раз деплой уже стёр их —
инцидент 13.08, восстановление из `.env.bak-*`.

```bash
# хаб
cd tilda-webhook && python3 scripts/deploy.py --dry-run   # посмотреть, что уйдёт
python3 scripts/deploy.py

# дашборд
cd "2. БИЗНЕС/08_Системное/_код/licensebridge-analytics"
bash deploy/up.sh              # + --collect, если нужен принудительный пересбор среза
```

Схема БД хаба создаётся сама (`init_db()` → `create_all`), отдельный шаг миграций не нужен.

### Asterisk

Правим конфиг в vault, копируем на сервер, перечитываем диалплан:

```bash
scp -i ~/.ssh/licensebridge_do asterisk/etc/extensions.conf root@159.65.97.156:/etc/asterisk/
ssh -i ~/.ssh/licensebridge_do root@159.65.97.156 'asterisk -rx "dialplan reload"'
```

Секреты диалплана лежат отдельно и в vault не попадают: `extensions_lb_local.conf`
(адрес хаба и `INTERNAL_API_KEY`), `pjsip_telnyx_auth.conf`, `manager_lb_local.conf`.
Смена пароля добавочного применяется только полным перезапуском Asterisk, не reload.

---

## 4. Флаги: чем управляется поведение

Всё живёт в `/opt/licensebridge-tilda-webhook/.env`, описание каждой переменной —
в `.env.example`. Меняем значение, затем `docker compose up -d` (пересборка не нужна).

| Флаг | Что включает |
|---|---|
| `ENABLE_LEADFLOW` | лид-машина: первое сообщение, AI-звонок Pleep, SMS. Вне лестницы раскатки |
| `ENABLE_TELEPHONY` | журнал звонков и задачи по пропущенным |
| `ENABLE_HANDOFF` | продажа переносится в воронку «Сборка» на Полину |
| `ENABLE_ASSIGNMENT`, `ENABLE_DEAL_DEDUP`, `ENABLE_CROSS_FUNNEL`, `ENABLE_CONTACT_SOFT_MERGE`, `ENABLE_LIGHT_CHAT_MERGE` | ручное «включить сейчас» поверх этапа авто-раскатки |
| `ALERT_NEW_LEAD` | уведомление в Telegram на каждую новую заявку |
| `DIAL_GUARD_MODE` | `block` — запрет повторного набора, `watch` — только считать нарушения |

### Авто-раскатка

Хаб сам двигается по лестнице: `0 shadow → 1 assignment → 2 deals → 3 contacts_no_chat
→ 4 contacts_light`. В shadow решения считаются, но Kommo не меняется. Текущий этап
хранится в БД, а не в `.env`, поэтому `SHADOW_MODE=true` в файле ещё не значит, что
система в тени — при `AUTO_ROLLOUT=true` решает этап.

```bash
# посмотреть этап и здоровье
ssh licensebridge-hub 'curl -s "http://127.0.0.1:8080/status?key=<INTERNAL_API_KEY>"'
# ручное управление
POST /internal/rollout {"action": "pause" | "resume" | "set_stage", "stage": N}
```

### Первое сообщение в WhatsApp

Отправляет хаб через Wazzup (`app/wazzup.py`), а не Salesbot в Kommo: бота
выключили в UI, и заявки с 26.08 по 03.09.2026 остались без ответа — снаружи это
выглядело как «Pleep не пишет первым». Ключ и канал уже в `.env`, отправка выключена.

Порядок включения:

1. Перенести четыре текста из Salesbot в `WA_TEMPLATES`, формат
   `1|текст||2|текст||3|текст||4|текст`, `{name}` подставит имя клиента.
2. Задать `WAZZUP_TEST_PHONE` — свой номер с WhatsApp. Пока он задан, сообщения
   уходят только на него, живым лидам хаб не пишет.
3. `ENABLE_WAZZUP_FIRST_TOUCH=true`, `docker compose up -d`, завести тестовую
   заявку и убедиться, что сообщение пришло.
4. Убрать `WAZZUP_TEST_PHONE` — заявки клиентов начинают получать ответ.

Одно сообщение на сделку навсегда: таблица `first_touch` с unique по `lead_id`,
повтор вебхука Kommo второго «здравствуйте» не отправит. Тихие часы сообщения не
задерживают, это ответ на заявку клиента; тихие часы действуют только на звонки.

Молчание клиента считает сам хаб: через `LEADFLOW_SILENCE_MIN` (30 минут) после
отправки, если в переписке нет ни одного входящего сообщения, воркер зовёт
голосовой агент — при `ENABLE_LEADFLOW=true`. Раньше это делал Salesbot, и без
этой части цепочка обрывалась бы на сообщении. Старше
`LEADFLOW_FOLLOWUP_MAX_HOURS` (48 часов) заявку уже не догоняем: звонок через три
дня — это обзвон, а не ответ на обращение.

```bash
# кому написали, кому нет и почему
ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && docker compose exec -T db \
  psql -U lbhub -d lbhub -c 'select lead_id, phone, variant, status, last_error from first_touch order by id desc limit 20;'"
```

### Блокеры набора

Лестница: 15 минут → 3 часа → следующий день → этап Reactivation, не больше 3 попыток
в сутки. Проверка стоит **на сервере телефонии до отправки вызова**, поэтому из
MicroSIP её не обойти. Менеджер слышит голосовое пояснение — файлы
`asterisk/sounds/lb-block-*.wav`.

---

## 5. Диагностика

### Preflight при старте worker

Перед первым циклом worker сверяет `.env` с живым Kommo: id аккаунта, воронок, этапов,
владельцев, полей. Настоящая ошибка — например неверный `PIPELINE_ID` — пишется как
`ERROR` и worker останавливается: работать с чужой воронкой хуже, чем не работать.

Некупленный SMS-бот (`SMS_BOT_ID=0`) — не ошибка, а `WARNING`: код этот случай
обрабатывает и вместо SMS ставит задачу менеджеру. Список таких пропусков —
`DEGRADATIONS` в `app/preflight.py`. Строку `preflight OK` в логе стоит увидеть после
каждого деплоя:

```bash
ssh licensebridge-hub 'docker logs lb-hub-worker 2>&1 | grep preflight | tail -5'
```

```bash
# хаб
ssh licensebridge-hub 'docker ps --format "{{.Names}} | {{.Status}}"'
ssh licensebridge-hub 'docker logs --since 2h lb-hub-worker | tail -50'

# журнал решений: что система сделала и почему
ssh licensebridge-hub "cd /opt/licensebridge-tilda-webhook && docker compose exec -T db \
  psql -U lbhub -d lbhub -c \"select created_at, action, detail from decisions order by id desc limit 20;\""

# телефония
ssh -i ~/.ssh/licensebridge_do root@159.65.97.156 'asterisk -rx "pjsip show endpoints"'
```

Добавочные: 101 Павел, 102 Полина, 103 Александра. `Unavailable` у добавочного значит,
что MicroSIP закрыт или не зарегистрирован — входящие на него не дойдут, и это самая
частая причина «звонок потерялся». Алерты offline об этом и предупреждают, это не
падение Telnyx.

Записи разговоров: `/var/spool/asterisk/monitor` на PBX, наружу только через хаб
`/rec/{подпись}/{файл}`.

---

## 6. Откат

- Код: `git log` по папке сервиса, `git checkout <коммит> -- <путь>`, затем деплой.
- `.env`: на сервере лежат `.env.bak-*` по датам правок.
- Kommo: снимок аккаунта в `backups/kommo_pre_nova_*`, порядок в его `RESTORE.md`.
- Перед удалением сущности хаб сам пишет JSON-снимок в таблицу `entity_snapshots`.
