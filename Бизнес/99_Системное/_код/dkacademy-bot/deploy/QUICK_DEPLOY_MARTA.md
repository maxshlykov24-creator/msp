# Быстрый деплой dkacademy-bot на VPS МартАче (без помех loyalty)

VPS: `72.56.12.104` (root). На нём уже работает `loyalty-service` на порту `127.0.0.1:18080`. Мы поставим `dkacademy-bot` параллельно: каталог `/root/dkacademy-bot/`, порт `127.0.0.1:19082`, отдельный compose-проект, отдельная БД, отдельный поддомен.

## Шаг 0. Один раз — добавить SSH-ключ агента на VPS

Публичный ключ агента (Cursor локально):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJfdP1QNFx6AS14AJnjZDyybNWutvbSho7WwbRG43sLl dkacademy-bot deploy max@cursor
```

Через панель Timeweb (web-консоль VNC) выполнить на VPS:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJfdP1QNFx6AS14AJnjZDyybNWutvbSho7WwbRG43sLl dkacademy-bot deploy max@cursor' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Локальная проверка с моей стороны (после добавления):

```bash
ssh marta-vps 'echo OK; hostname; docker ps --format "{{.Names}}"; ls /root | head'
```

После «OK ...» дальше всё делает агент.

## Шаг 1. На стороне Максима в панели Timeweb

1. Создать **новый бесплатный поддомен** `dkacademy-bot-XXXXX.twc1.net` → A-запись на `72.56.12.104`. Дождаться разлёта DNS (`dig +short dkacademy-bot-XXXXX.twc1.net` должен вернуть `72.56.12.104`).
2. Сообщить агенту FQDN.

## Шаг 2. Подготовить значения `.env` (агент пропишет на сервере)

Минимум:

| Переменная | Что положить |
|------------|--------------|
| `TELEGRAM_BOT_TOKEN` | новый из BotFather (старый отозвать `/revoke`) |
| `LIVEINFORM_API_ID` | актуальный из кабинета DKAcademy (после ротации) |
| `LIVEINFORM_WEBHOOK_SECRET` | случайная строка (`openssl rand -hex 16`) |
| `AMO_SUBDOMAIN` | например `dkacademy` |
| `AMO_CLIENT_ID`, `AMO_CLIENT_SECRET` | из карточки интеграции amo |
| `AMO_LONG_LIVED_TOKEN` | если без OAuth, на старте |
| `AMO_FIELD_LEAD_CDEK` | числовой `field_id` поля «Номер СДЭК» на сделке |
| `AMO_FIELD_LEAD_LIVEINFORM_STATUS` | числовой `field_id` поля «Статус LiveInform» на сделке |
| `AMO_FIELD_CONTACT_TELEGRAM_CHAT_ID` | поле текста на контакте |
| `AMO_FIELD_CONTACT_TELEGRAM_USER_ID` | (опционально) |
| `AMO_FIELD_CONTACT_BOT_STARTED_AT` | если есть |
| `AMO_FIELD_CONTACT_BOT_STARTED_AT_TYPE` | `text` или `date` (по типу поля в amo) |
| `ONBOARDING_CONTACT_NOT_FOUND` | `create` или `reject` |

`REDIRECT_URI` пропишется автоматически после `timeweb-nginx-subdomain.sh`.

## Шаг 3. Что выполнит агент после готовности

```bash
# rsync кода (только из dkacademy-bot/, без .env)
rsync -avz --exclude '.env' --exclude '__pycache__' --exclude '.venv' \
  /Users/max/Desktop/CURSOR/Бизнес/99_Системное/_код/dkacademy-bot/ marta-vps:/root/dkacademy-bot/

# инициализация .env на сервере (заполнит агент по присланным значениям)
ssh marta-vps 'cd /root/dkacademy-bot && [ -f .env ] || cp .env.example .env'

# поднять стек (изоляция: compose name=dkacademy-bot, порт 19082, volume dkacademy_bot_pg)
ssh marta-vps 'cd /root/dkacademy-bot && bash deploy/timeweb-up.sh'

# nginx + LE на новый поддомен (loyalty не трогаем)
ssh marta-vps 'cd /root/dkacademy-bot && DKACADEMY_FQDN=<FQDN> bash deploy/timeweb-nginx-subdomain.sh'

# перечитать .env с REDIRECT_URI
ssh marta-vps 'cd /root/dkacademy-bot && docker compose up -d'
```

Smoke агент прогонит сразу.

## Откат

```bash
ssh marta-vps 'cd /root/dkacademy-bot && docker compose down'
ssh marta-vps 'rm -f /etc/nginx/sites-enabled/dkacademy-bot-subdomain && nginx -t && systemctl reload nginx'
ssh marta-vps 'certbot delete --cert-name <FQDN>'   # опционально
```
