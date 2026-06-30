# MANSBAND Касса

Монорепо кассы MANSBAND. Касса — **единственный мост** между amoCRM (мозг: сделки/этапы)
и МойСклад (каталог/остатки/документы). Старая интеграция amoCRM↔МойСклад отключена.

## Структура

```
kassa/
  packages/shared   — общие типы (Deal/Payment/...) + zod-схемы + константы
  apps/api          — бэкенд: Fastify + Drizzle + PostgreSQL
  apps/web          — фронтенд: React + Vite (перенос прототипа), PWA
  deploy/           — nginx (host), скрипты setup-vps / deploy / backup
  docker-compose.yml — db + api + web
```

## Что делает

- **Фаза 0:** монорепо, HTTPS-сайт под `mansband-kassa.ru`, вход по логину (JWT), PWA.
- **Фаза 1:** чтение реальных заявок из amoCRM (список/поиск/карточка), синк справочников.
- **Фаза 2:** каталог/остатки МойСклад; проведение продажи (контакт+сделка+Успех →
  заказ+отгрузка+входящий платёж в МойСклад), фотофиксации → файлы документа,
  writeback ссылок и статуса оплаты в amo. Идемпотентность.
- **Фаза 3:** webhooks amoCRM+МойСклад + WebSocket-push + поллинг-фолбэк.
- **Фаза 4:** ledger, сертификаты, очередь Эдвина (Telegram), уведомления, закрытие смены.
- **Фаза 5:** возвраты/обмены/дефекты, продажа компании, статистика/KPI, Сары.

## Локальная разработка

Требуется Node 20+ и pnpm 9+ (или Docker).

```bash
pnpm install
pnpm --filter @kassa/shared build      # shared собирается первым (его dist используют api/web)
# БД (через docker) или свой PostgreSQL; задать DATABASE_URL
cp .env.example .env                    # заполнить
pnpm db:migrate                         # применить миграции
pnpm seed                               # создать пользователей (распечатает пароли)
pnpm dev:api                            # бэкенд на :3001
pnpm dev:web                            # фронт на :5173 (проксирует /api,/ws на :3001)
```

Демо-режим без бэкенда: `VITE_USE_MOCK=1 pnpm dev:web`.

## Деплой на VPS (без GitHub, по rsync)

VPS: `72.56.240.103`, домен `mansband-kassa.ru` (A-запись уже настроена).

1. **Подготовка сервера** (один раз), на VPS под root:
   ```bash
   bash setup-vps.sh   # docker, ufw, fail2ban, nginx, certbot
   ```
2. **Секреты:** на сервере создать `/opt/mansband-kassa/.env` из `.env.example`
   (значения брать из `MANSBAND/_private/kassa_access.env`).
3. **Деплой** (с локальной машины из корня `kassa/`):
   ```bash
   bash deploy/deploy.sh        # rsync + docker compose up -d --build + миграции
   docker compose run --rm api node dist/seed.js   # на сервере, первый раз — создать юзеров
   ```
4. **HTTPS (host nginx + certbot)** на VPS:
   ```bash
   # в /etc/nginx/nginx.conf в http{} добавить (если нет):
   #   map $http_upgrade $connection_upgrade { default upgrade; '' close; }
   sudo cp deploy/nginx/mansband-kassa.conf /etc/nginx/sites-available/mansband-kassa
   sudo ln -sf /etc/nginx/sites-available/mansband-kassa /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d mansband-kassa.ru -d www.mansband-kassa.ru -m info@msproduct.ru --agree-tos
   ```
5. **Бэкапы БД:** добавить в cron `deploy/backup-db.sh` (ежедневно).

Обновление после правок: повторить `bash deploy/deploy.sh`.

## Безопасность

- Все секреты только в `.env` (сервер) и `_private/` (локально) — в `.gitignore`.
- Токены amoCRM/МойСклад, засвеченные в переписке, **перевыпустить после запуска**.
- Пользователи Максим и Миша — админы; пароли меняются при первом входе.
