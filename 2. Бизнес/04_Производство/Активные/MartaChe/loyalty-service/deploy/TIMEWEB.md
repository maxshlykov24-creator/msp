# Прод-окружение Timeweb Cloud (MartaChe loyalty-service)

Параметры (актуальные):

| Параметр | Значение |
|----------|-----------|
| Кастомный домен | `it-martache.ru` (выпущен LE-сертификат) |
| Публичный IPv4 | `72.56.12.104` |
| Webhook URL | `https://it-martache.ru/webhook/moysklad` |

## Быстрый старт на сервере (тот же хост, что Tilda)

На VPS, в каталоге с проектом:

```bash
./deploy/timeweb-up.sh
```

Стартует [docker-compose.timeweb.yml](../docker-compose.timeweb.yml): Postgres внутри сети Docker, **API на `127.0.0.1:18080`**. Потом вшить фрагмент в nginx (см. ниже).

## Нужен ли отдельный сервер, если Tilda-hook уже тут?

**Нет, отдельный «второй сайт» не обязателен.** Tilda-интеграция и loyalty-service **спокойно уживаются**:

| Вариант | Что сделать |
|--------|-------------|
| **A. Один домен** | Скрипт `./deploy/timeweb-up.sh` + [docker-compose.timeweb.yml](../docker-compose.timeweb.yml). Nginx: `location /webhook/moysklad` → `http://127.0.0.1:18080`, остальное — Tilda. См. [nginx/loyalty-same-host.example.conf](nginx/loyalty-same-host.example.conf). |
| **B. Поддомен на этот же VPS (рекомендую, Tilda на основном домене не трогать)** | В панели: новый поддомен `*.twc1.net` → **A-запись** на **публичный IPv4** сервера с loyalty. На VPS: [timeweb-nginx-subdomain.sh](timeweb-nginx-subdomain.sh) + [nginx/loyalty-subdomain.conf](nginx/loyalty-subdomain.conf) — Nginx+HTTPS, `WEBHOOK_PUBLIC_URL` → `register_webhooks.py`. |

Смысл: МойСклад вызывает **только** `POST /webhook/moysklad` — он не пересекается с типичными путями Tilda, если в nginx отдельный `location`.

## URL вебхука МойСклад

Полный URL (если A и nginx на том же хосте):

`https://neurosell-tilda-ms-order-hook-5f6f.twc1.net/webhook/moysklad`

В `.env` — `WEBHOOK_PUBLIC_URL` на этот адрес (уже в репо).

Проверка снаружи (после nginx): `GET https://<твой-домен>/health` → `{"status":"ok"}`.

### Поддомен: что сделать в панели и на сервере

1. **Timeweb:** привяжи новый бесплатный `*.twc1.net` к **этому** VPS (или в DNS: **A** на **IPv4** машины, где `loyalty`). Включи **публичный IPv4**, если его ещё нет.
2. **VPS** (когда A запись пингуется снаружи):
   ```bash
   export LOYALTY_FQDN=loyalty-xxxxx.twc1.net
   # опционально: export CERTBOT_EMAIL=you@example.com
   bash /root/loyalty-service/deploy/timeweb-nginx-subdomain.sh
   ```
3. **Регистрация в МойСклад:** `cd /root/loyalty-service && . .venv/bin/activate && python scripts/register_webhooks.py`

Проверка снаружи: `https://$LOYALTY_FQDN/health` → `{"status":"ok"}`.

**Если certbot пишет про лимит Let’s Encrypt на `twc1.net`:** на общей бесплатной зоне часто упираются в *50 сертификатов/неделю на весь* `twc1.net` — повтори выпуск **после** времени `retry after` в логе, либо включи **бесплатный SSL в панели Timeweb** для этого поддомена (часто без своего certbot на VPS).  
**VPS без публичного IPv4:** для LE по HTTP-01 в DNS у поддомена должен быть **AAAA** на этот сервер, либо **включи публичный IPv4** в панели и **A-запись** на него, затем снова `bash deploy/timeweb-nginx-subdomain.sh` (после снятия лимита или с сертификатом из панели).

**Сервер (автоповторы):** на VPS могут стоять **at(1)**: `certbot` ~после снятия лимита LE, затем попытка `register_webhooks.py` (см. `/root/letsencrypt-retry.log`, `/root/register_webhooks.log`). **Без A-записи** поддомен → **публичный IPv4/AAAA** твоей VPS **в панели** Let’s Encrypt не сможет пройти проверку. ZeroSSL в **acme.sh** без EAB/аккаунта в ZeroSSL **не** выпустит; надёжный путь — **LE после окна** или **SSL в панели Timeweb**.

## Регистрация вебхуков в МойСклад

```bash
cd loyalty-service
python scripts/register_webhooks.py
```

Подписывает: `customerorder` (CREATE/UPDATE/DELETE), `counterparty` (UPDATE), `bonustransaction` (CREATE).

(Берёт `WEBHOOK_PUBLIC_URL` и `MS_TOKEN` из `.env`.)

## Деплой обновлений

```bash
ssh root@72.56.12.104
cd /root/loyalty-service && git pull
. .venv/bin/activate && pip install -r requirements.txt
# при необходимости — заполни в .env новые ATTR_ORDER_*, ATTR_LOYALTY_STATUS, ATTR_ACTIVE_BONUSES, ATTR_PENDING_BONUSES
python scripts/discover_metadata.py   # узнать UUID полей
systemctl restart loyalty.service
python scripts/register_webhooks.py   # один раз — добавить новые подписки
```

`init_db()` сам сделает `ALTER TABLE` для новых колонок (`processed_events`, `bonus_batches`, `loyalty_members`).

## Текущая логика регистрации ПЛ

Новые контрагенты **не регистрируются в программе лояльности автоматически**.

Регистрация происходит только из заказа покупателя: менеджер вручную ставит в заказе `Статус ПЛ = Активен` и выбирает `Уровень ПЛ`. После этого сервис создаёт участника в локальной БД, синхронизирует поля контрагента и начисляет `REGISTRATION_WELCOME_BONUS_POINTS` приветственных бонусов (по умолчанию `300`) со сроком жизни 365 дней.
