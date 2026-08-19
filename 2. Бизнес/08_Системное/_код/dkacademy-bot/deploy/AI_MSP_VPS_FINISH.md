# Финал деплоя dkacademy-bot — VPS ai-msp

> Сервер уже поднят, код развернут, контейнеры работают, /health отдаёт ok.
> Остались только шаги от владельца — каждый занимает 1–5 минут.

## Координаты сервера

| Параметр | Значение |
|---|---|
| FQDN (целевой) | `dkacademy-bot.ai-msp.ru` |
| IPv4 | `194.87.226.234` |
| SSH | `ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234` |
| Папка проекта на VPS | `/root/dkacademy-bot/` |
| .env на VPS | `/root/dkacademy-bot/.env` (chmod 600, root only) |
| API внутри | `127.0.0.1:19082` (за Nginx) |
| Docker compose project | `dkacademy-bot` |
| Webhook secret (для LiveInform) | в `/root/dkacademy-bot/.env`, ключ `LIVEINFORM_WEBHOOK_SECRET` |

Что уже сделано на VPS:

- Ubuntu 24.04 LTS, MSK TZ, 1 GB swap.
- UFW открыт только на 22/80/443; fail2ban + unattended-upgrades.
- SSH: только по ключу, парольная аутентификация отключена, root-пароль ротирован.
- Docker + compose plugin, Nginx, certbot — установлены, автостарт включён.
- `dkacademy-bot` собран и запущен (`db` healthy, `api` отдаёт `/health=ok`).
- Nginx vhost `dkacademy-bot.ai-msp.ru` → proxy `127.0.0.1:19082` готов (HTTP).
- `restart: unless-stopped` для контейнеров → переживут reboot VPS.

---

## Шаг 1. DNS A-запись (Timeweb → Домены → ai-msp.ru → DNS)

Добавить **одну** запись:

| Поле | Значение |
|---|---|
| Тип | `A` |
| Имя/Поддомен | `dkacademy-bot` |
| Значение/IP | `194.87.226.234` |
| TTL | `300` |

Корневой `ai-msp.ru`, `www`, MX и прочие записи — **не трогать**.

Проверка через 1–10 минут:

```bash
dig +short dkacademy-bot.ai-msp.ru
# должно вернуть: 194.87.226.234
```

---

## Шаг 2. Выпустить HTTPS-сертификат (после DNS)

Одной командой по SSH:

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  'certbot --nginx -d dkacademy-bot.ai-msp.ru \
     --non-interactive --agree-tos --register-unsafely-without-email \
     --redirect'
```

Результат: автообновление подцепится через `certbot.timer` (уже включён).

Smoke-проверка извне:

```bash
curl -sS https://dkacademy-bot.ai-msp.ru/health
# {"status":"ok"}
```

---

## Шаг 3. Заполнить реальные секреты в .env

На сервере открыть `/root/dkacademy-bot/.env` и подставить значения вместо пустых.

### 3.1 Telegram (новый бот после ротации)

1. У `@BotFather` создать бота **DKAcademyDelivery** (или ротировать токен у существующего).
2. Скопировать токен.
3. На VPS:
   ```bash
   ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
     "sed -i 's|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=ВСТАВИТЬ_ТОКЕН|' /root/dkacademy-bot/.env"
   ```

### 3.2 LiveInform `api_id` (после ротации в кабинете)

В личном кабинете LiveInform → Интеграции → сгенерировать новый `api_id`. Затем:

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  "sed -i 's|^LIVEINFORM_API_ID=.*|LIVEINFORM_API_ID=ВСТАВИТЬ_API_ID|' /root/dkacademy-bot/.env"
```

### 3.3 amoCRM OAuth

В amo → Настройки → Интеграции → Создать интеграцию (или взять существующую):

- `AMO_CLIENT_ID` — UUID интеграции.
- `AMO_CLIENT_SECRET` — секретный ключ.
- `REDIRECT_URI` — уже стоит `https://dkacademy-bot.ai-msp.ru/oauth/callback`. Этот же URL вписать в карточке интеграции в amo.
- `AMO_SUBDOMAIN` — субдомен (поправить, если не `dkacademy`).

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  "sed -i \
     -e 's|^AMO_CLIENT_ID=.*|AMO_CLIENT_ID=...|' \
     -e 's|^AMO_CLIENT_SECRET=.*|AMO_CLIENT_SECRET=...|' \
     -e 's|^AMO_SUBDOMAIN=.*|AMO_SUBDOMAIN=dkacademy|' \
     /root/dkacademy-bot/.env"
```

Альтернатива OAuth — `AMO_LONG_LIVED_TOKEN` (долгоживущий токен из карточки интеграции). Тогда `AMO_CLIENT_ID/SECRET` можно оставить пустыми; OAuth-flow не понадобится.

### 3.4 amo: ID кастомных полей (числа)

В amo, в карточке любой сделки/заказа/контакта, открыть исходник страницы или взять из API списка полей:

- `AMO_FIELD_LEAD_CDEK` — поле «Номер СДЭК» в сделке.
- `AMO_FIELD_LEAD_LIVEINFORM_STATUS` — поле «Статус LiveInform» в сделке.
- `AMO_FIELD_CONTACT_TELEGRAM_CHAT_ID`, `AMO_FIELD_CONTACT_TELEGRAM_USER_ID`, `AMO_FIELD_CONTACT_BOT_STARTED_AT` — поля в контакте.
- `AMO_FIELD_CONTACT_BOT_STARTED_AT_TYPE=date` если поле в amo — дата (Unix-timestamp), `text` если строка.

Для «заказа покупателя», если он реализован через каталог:

```env
AMO_ORDER_ENTITY=catalog
AMO_CATALOG_ID=<id каталога>
AMO_FIELD_CATALOG_CDEK=<id поля СДЭК в каталоге>
AMO_FIELD_CATALOG_LIVEINFORM_STATUS=<id поля статус LI в каталоге>
```

Если «заказ» — это просто ещё одна сделка (lead), оставить `AMO_ORDER_ENTITY=lead_only`.

### 3.5 Применить .env (перезапустить api)

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  'cd /root/dkacademy-bot && docker compose up -d'
```

### 3.6 Проверить, что Telegram-polling завёлся

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  'docker compose -f /root/dkacademy-bot/docker-compose.yml logs --tail=30 api | grep -i -E "(bot|polling|telegram|error)"'
```

В логах должно появиться что-то про `dispatcher start` / успешный getMe.

---

## Шаг 4. amoCRM OAuth-авторизация (только если используете OAuth, не long-lived)

Открыть в браузере:

```
https://dkacademy-bot.ai-msp.ru/internal/oauth-link
```

Перейти по сгенерированной ссылке, авторизовать интеграцию в amo. После редиректа в БД сохранятся access/refresh токены.

С `AMO_LONG_LIVED_TOKEN` этот шаг пропускается.

---

## Шаг 5. Зарегистрировать webhook в LiveInform

В кабинете LiveInform → Webhooks (или Интеграции → Webhooks):

| Поле | Значение |
|---|---|
| URL | `https://dkacademy-bot.ai-msp.ru/webhooks/liveinform` |
| Метод | `POST` |
| Заголовок | `X-LiveInform-Secret: <значение из .env LIVEINFORM_WEBHOOK_SECRET>` |
| События | смена статуса трека (или «все события доставки») |

Узнать webhook-секрет:

```bash
ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  'grep ^LIVEINFORM_WEBHOOK_SECRET /root/dkacademy-bot/.env'
```

> Если LiveInform не поддерживает кастомный заголовок — вытащить значение из `.env` и положить в любой механизм подписи, который их интерфейс предлагает; на стороне сервиса проверка делается по `LIVEINFORM_WEBHOOK_HEADER` (по умолчанию `X-LiveInform-Secret`).

Smoke-тест webhook извне (после регистрации можно запустить вручную):

```bash
SECRET=$(ssh -i ~/.ssh/dkacademy_marta_ed25519 root@194.87.226.234 \
  'grep ^LIVEINFORM_WEBHOOK_SECRET /root/dkacademy-bot/.env | cut -d= -f2')
curl -sS -X POST https://dkacademy-bot.ai-msp.ru/webhooks/liveinform \
  -H "Content-Type: application/json" \
  -H "X-LiveInform-Secret: $SECRET" \
  -d '{"liveinform_id":"smoke-001"}' \
  -o /dev/null -w "HTTP %{http_code}\n"
# HTTP 200
```

В логах api увидите попытку запросить статус по треку smoke-001 (упадёт, т.к. id вымышленный — это нормально).

---

## Полезные команды на VPS

```bash
# Логи api (live)
docker compose -f /root/dkacademy-bot/docker-compose.yml logs -f api

# Перезапуск
docker compose -f /root/dkacademy-bot/docker-compose.yml restart api

# Полный передеплой (после правок кода через rsync)
docker compose -f /root/dkacademy-bot/docker-compose.yml up -d --build

# Проверка диска / памяти
df -h /; free -h; docker system df

# Ротация webhook-secret
NEW=$(tr -dc "A-Za-z0-9" </dev/urandom|head -c40)
sed -i "s|^LIVEINFORM_WEBHOOK_SECRET=.*|LIVEINFORM_WEBHOOK_SECRET=$NEW|" /root/dkacademy-bot/.env
docker compose -f /root/dkacademy-bot/docker-compose.yml up -d
echo "Новый secret: $NEW (вписать в LiveInform)"
```

## Безопасность

- **Пароль root, который Timeweb сгенерировал при создании сервера** и был отправлен в чат — **сменён** на случайный 48-символьный (нигде не сохранён). SSH по паролю отключён. В случае утери ключа — сменить пароль через Timeweb-панель и заново настроить ключ.
- Доступы (FQDN, путь к .env, ssh-команда) можно занести в `Бизнес/.../ДОСТУПЫ.md` — но **без секретов** в файле.
- `.env` на сервере — режим 600, только root.
